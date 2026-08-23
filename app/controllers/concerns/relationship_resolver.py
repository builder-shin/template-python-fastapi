"""Relationship actions plus linkage resolution, validation, and assignment.

Second layer of the ``CrudActions`` chain. It owns the three relationship routes'
action bodies, the public ``assign_relationships`` hook, and every helper that turns
JSON:API linkage into mapped instances (or back).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from fastapi import Request
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value
from starlette.responses import Response

from app.controllers.concerns.crud_base import CrudDeclarations
from app.controllers.concerns.document_parsing import (
    coerce_model_id,
    linkage_data,
    reject_query_parameters,
)
from app.controllers.concerns.route_registrar import RelationshipMutation
from app.jsonapi import (
    JsonApiException,
    JsonApiResponse,
    RelationshipDocument,
    ResourceIdentifier,
)
from app.jsonapi.naming import snake_to_camel
from app.jsonapi.query import apply_pagination, build_pagination_links, parse_page_query
from app.serializers.base import RelationshipDefinition


class CrudRelationships[
    ModelT,
    CreateT: BaseModel,
    UpdateT: BaseModel,
    ReplaceT: BaseModel,
](CrudDeclarations[ModelT, CreateT, UpdateT, ReplaceT]):
    """Serve and mutate serializer-declared relationships."""

    def show_relationship(
        self,
        resource_id: str,
        public_name: str,
        request: Request,
        session: Session,
    ) -> JsonApiResponse:
        """Return linkage for one serializer-declared relationship."""

        reject_query_parameters(request)
        definition = self._relationship_definition(public_name)
        model = self._find_resource(session, resource_id)
        related = getattr(model, definition.attribute)
        linkage = self._relationship_linkage(definition, related)
        return JsonApiResponse(
            RelationshipDocument(
                data=linkage,
                links={
                    "self": f"{self.prefix}/{resource_id}/relationships/{public_name}",
                    "related": f"{self.prefix}/{resource_id}/{public_name}",
                },
            )
        )

    def show_related(
        self,
        resource_id: str,
        public_name: str,
        request: Request,
        session: Session,
    ) -> JsonApiResponse:
        """Return the resource document addressed by a related-resource URL.

        The related rows are queried directly with the *target* serializer's loader
        options, because the response is serialized by that serializer and would raise on
        its own unloaded relationships otherwise. A to-many related URL is paginated with
        the same ``page[number]``/``page[size]`` contract as the collection URL; a to-one
        related URL keeps rejecting every query parameter.
        """

        definition = self._relationship_definition(public_name)
        page = parse_page_query(request.query_params) if definition.many else None
        if page is None:
            reject_query_parameters(request)

        coerced_id = self._require_resource_id(session, resource_id)
        target_model = self._relationship_target_model(definition)
        statement: Select[Any] = (
            select(target_model)
            .select_from(self.model_class)
            .join(getattr(self.model_class, definition.attribute))
            .where(self._model_mapper().primary_key[0] == coerced_id)
        )
        options = definition.serializer.loader_options(target_model)

        if page is None:
            related = session.scalars(statement.options(*options)).unique().one_or_none()
            return JsonApiResponse(definition.serializer.document(related))

        count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
        total = session.scalar(count_statement) or 0
        target_primary_key = sqlalchemy_inspect(target_model).primary_key[0]
        paged = apply_pagination(statement.order_by(target_primary_key.asc()).options(*options), page)
        related_models = list(session.scalars(paged).unique().all())
        document = definition.serializer.document(related_models).model_copy(
            update={
                "links": build_pagination_links(
                    request.url.path,
                    request.query_params,
                    page,
                    total=total,
                ),
                "meta": {"totalCount": total},
            }
        )
        return JsonApiResponse(document)

    def mutate_relationship(
        self,
        resource_id: str,
        public_name: str,
        mutation: RelationshipMutation,
        request: Request,
        document: BaseModel,
        session: Session,
    ) -> Response:
        """Add, replace, or remove linkage in the parent resource transaction."""

        reject_query_parameters(request)
        definition = self._relationship_definition(public_name)
        if not definition.many and mutation != "replace":
            raise JsonApiException(status_code=400, code="INVALID_JSONAPI_DOCUMENT")
        linkage = linkage_data(document)

        with session.begin():
            model = self._find_resource(session, resource_id, for_update=True)
            resolved = self._resolve_relationship_data(
                session,
                definition,
                linkage,
                pointer_prefix="/data",
            )
            if not definition.many:
                setattr(model, definition.attribute, resolved)
            else:
                current = list(getattr(model, definition.attribute))
                requested = cast(list[object], resolved)
                if mutation == "replace":
                    updated = requested
                elif mutation == "add":
                    existing_keys = {self._model_identity(item) for item in current}
                    updated = [
                        *current,
                        *(item for item in requested if self._model_identity(item) not in existing_keys),
                    ]
                else:
                    removed_keys = {self._model_identity(item) for item in requested}
                    updated = [item for item in current if self._model_identity(item) not in removed_keys]
                setattr(model, definition.attribute, updated)
            session.flush()

        return Response(status_code=204)

    def assign_relationships(
        self,
        session: Session,
        model: ModelT,
        relationships: BaseModel | None,
    ) -> None:
        """Resolve and assign only serializer-declared relationship input."""

        if relationships is None:
            return
        for attribute, value in self._relationship_assignments(session, relationships).items():
            state = sqlalchemy_inspect(model, raiseerr=False)
            if state is not None and state.transient:
                set_committed_value(model, attribute, value)
            else:
                setattr(model, attribute, value)

    def _relationship_assignments(
        self,
        session: Session,
        relationships: BaseModel | None,
    ) -> dict[str, object]:
        if relationships is None:
            return {}
        assignments: dict[str, object] = {}
        for field_name, field in type(relationships).model_fields.items():
            if field_name not in relationships.model_fields_set:
                continue
            public_name = field.alias or snake_to_camel(field_name)
            definition = self.serializer_class.relationships.get(public_name)
            if definition is None:
                raise JsonApiException(
                    status_code=400,
                    code="INVALID_JSONAPI_DOCUMENT",
                    source_pointer=f"/data/relationships/{public_name}",
                )
            relationship_document = getattr(relationships, field_name)
            linkage = linkage_data(relationship_document)
            resolved = self._resolve_relationship_data(
                session,
                definition,
                linkage,
                pointer_prefix=f"/data/relationships/{public_name}/data",
            )
            assignments[definition.attribute] = resolved
        return assignments

    def _relationship_definition(self, public_name: str) -> RelationshipDefinition:
        definition = self.serializer_class.relationships.get(public_name)
        if definition is None:
            raise JsonApiException(status_code=404, code="RESOURCE_NOT_FOUND")
        return definition

    def _relationship_target_model(self, definition: RelationshipDefinition) -> type[Any]:
        mapper = self._model_mapper()
        try:
            relationship = mapper.relationships[definition.attribute]
        except KeyError as error:
            raise RuntimeError(
                f"serializer relationship {definition.attribute!r} is not mapped on {self.model_class.__name__}"
            ) from error
        if relationship.uselist is not definition.many:
            raise RuntimeError(
                f"serializer relationship {definition.attribute!r} cardinality does not match SQLAlchemy"
            )
        return cast(type[Any], relationship.mapper.class_)

    def _resolve_relationship_data(
        self,
        session: Session,
        definition: RelationshipDefinition,
        linkage: object,
        *,
        pointer_prefix: str,
    ) -> object | list[object] | None:
        if definition.many:
            if not isinstance(linkage, list):
                raise JsonApiException(
                    status_code=400,
                    code="INVALID_JSONAPI_DOCUMENT",
                    source_pointer=pointer_prefix,
                )
            identifiers = linkage
        else:
            if linkage is None:
                return None
            if not isinstance(linkage, ResourceIdentifier):
                raise JsonApiException(
                    status_code=400,
                    code="INVALID_JSONAPI_DOCUMENT",
                    source_pointer=pointer_prefix,
                )
            identifiers = [linkage]

        target_model = self._relationship_target_model(definition)
        target_mapper = sqlalchemy_inspect(target_model)
        if target_mapper is None:
            raise RuntimeError("relationship target model is not mapped")
        primary_key = target_mapper.primary_key[0]
        coerced_ids: list[object] = []
        seen_ids: set[object] = set()
        for index, identifier in enumerate(identifiers):
            item_pointer = f"{pointer_prefix}/{index}" if definition.many else pointer_prefix
            if identifier.type != definition.serializer.type_name:
                raise JsonApiException(
                    status_code=409,
                    code="TYPE_MISMATCH",
                    source_pointer=f"{item_pointer}/type",
                )
            coerced_id = coerce_model_id(
                target_model,
                identifier.id,
                relationship=True,
                source_pointer=f"{item_pointer}/id",
            )
            if coerced_id in seen_ids:
                raise JsonApiException(
                    status_code=400,
                    code="INVALID_JSONAPI_DOCUMENT",
                    source_pointer=f"{item_pointer}/id",
                )
            seen_ids.add(coerced_id)
            coerced_ids.append(coerced_id)

        if not coerced_ids:
            return []
        statement = select(target_model).where(primary_key.in_(coerced_ids))
        found = list(session.scalars(statement).all())
        found_by_id = {self._model_identity(item): item for item in found}
        ordered: list[object] = []
        for index, coerced_id in enumerate(coerced_ids):
            item = found_by_id.get(coerced_id)
            if item is None:
                item_pointer = f"{pointer_prefix}/{index}" if definition.many else pointer_prefix
                raise JsonApiException(
                    status_code=404,
                    code="RELATIONSHIP_RESOURCE_NOT_FOUND",
                    source_pointer=f"{item_pointer}/id",
                )
            ordered.append(item)
        return ordered if definition.many else ordered[0]

    def _relationship_linkage(
        self,
        definition: RelationshipDefinition,
        related: object,
    ) -> ResourceIdentifier | list[ResourceIdentifier] | None:
        if definition.many:
            if not isinstance(related, Sequence) or isinstance(related, (str, bytes, bytearray)):
                raise RuntimeError(f"relationship {definition.attribute!r} did not return a collection")
            return [self._resource_identifier(definition, item) for item in related]
        if related is None:
            return None
        return self._resource_identifier(definition, related)

    @staticmethod
    def _resource_identifier(
        definition: RelationshipDefinition,
        model: object,
    ) -> ResourceIdentifier:
        return ResourceIdentifier(
            type=definition.serializer.type_name,
            id=str(CrudRelationships._model_identity(model)),
        )

    @staticmethod
    def _model_identity(model: object) -> object:
        state = sqlalchemy_inspect(model, raiseerr=False)
        if state is None:
            raise RuntimeError(f"relationship resource {type(model).__name__} is not mapped")
        if state.identity:
            return state.identity[0]
        mapper = state.mapper
        return getattr(model, mapper.primary_key[0].key)

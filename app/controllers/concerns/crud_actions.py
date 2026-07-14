"""Rails-style inherited CRUD actions for JSON:API resources."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from hashlib import blake2b
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel
from pydantic.experimental.missing_sentinel import MISSING
from sqlalchemy import Select, func, select
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value
from starlette.responses import Response

from app.controllers.concerns.jsonapi_routes import (
    JsonApiRoute,
    relationship_document_model,
    write_document_model,
)
from app.jsonapi import (
    JSONAPI_MEDIA_TYPE,
    ErrorDocument,
    JsonApiException,
    JsonApiResponse,
    RelationshipDocument,
    ResourceIdentifier,
    SuccessDocument,
    require_jsonapi_accept,
)
from app.jsonapi.query import (
    QueryPolicy,
    apply_filters,
    apply_pagination,
    apply_sort,
    build_pagination_links,
    parse_include_query,
    parse_query,
)
from app.serializers.base import JsonApiSerializer, RelationshipDefinition
from config.database import get_session


def _snake_to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(segment[:1].upper() + segment[1:] for segment in tail)


_JSONAPI_BODY = Body(..., media_type=JSONAPI_MEDIA_TYPE)
_SESSION_DEPENDENCY = Depends(get_session)


type RelationshipMutation = Literal["add", "replace", "remove"]


_ERROR_DESCRIPTIONS = {
    400: "Invalid JSON:API request",
    403: "Forbidden",
    404: "Resource not found",
    406: "Not acceptable",
    409: "Resource conflict",
    415: "Unsupported media type",
    422: "Validation error",
    500: "Internal server error",
}


def _jsonapi_error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {
        status_code: {
            "description": _ERROR_DESCRIPTIONS[status_code],
            "model": ErrorDocument,
        }
        for status_code in status_codes
    }


class CrudActions[
    ModelT,
    CreateT: BaseModel,
    UpdateT: BaseModel,
    ReplaceT: BaseModel,
]:
    """Reusable CRUD controller with overridable Rails-like lifecycle hooks."""

    model_class: type[ModelT]
    serializer_class: type[JsonApiSerializer[ModelT]]
    create_schema: type[CreateT]
    update_schema: type[UpdateT]
    replace_schema: type[ReplaceT]
    relationships_schema: type[BaseModel] | None = None
    query_policy: QueryPolicy
    enable_upsert = False

    def __init__(self, *, prefix: str, tags: list[str]) -> None:
        if not prefix.startswith("/") or prefix.endswith("/"):
            raise ValueError("CRUD prefix must start with '/' and must not end with '/'")
        self.prefix = prefix
        self.router = APIRouter(
            prefix=prefix,
            tags=cast(list[str | Enum], tags),
            dependencies=[Depends(require_jsonapi_accept)],
            route_class=JsonApiRoute,
        )
        self._create_document_schema = write_document_model(
            name=f"{type(self).__name__}Create",
            attributes_schema=self.create_schema,
            require_attributes=True,
            require_id=False,
            relationships_schema=self.relationships_schema,
        )
        self._update_document_schema = write_document_model(
            name=f"{type(self).__name__}Update",
            attributes_schema=self.update_schema,
            require_attributes=False,
            require_id=True,
            relationships_schema=self.relationships_schema,
        )
        self._replace_document_schema = write_document_model(
            name=f"{type(self).__name__}Replace",
            attributes_schema=self.replace_schema,
            require_attributes=True,
            require_id=True,
            relationships_schema=self.relationships_schema,
        )
        self._writable_relationship_names = frozenset(
            field.alias or _snake_to_camel(field_name)
            for field_name, field in (
                self.relationships_schema.model_fields.items() if self.relationships_schema is not None else ()
            )
        )
        self._relationship_document_schemas = {
            public_name: relationship_document_model(
                name=f"{type(self).__name__}{public_name[:1].upper()}{public_name[1:]}Relationship",
                many=definition.many,
            )
            for public_name, definition in self.serializer_class.relationships.items()
            if public_name in self._writable_relationship_names
        }
        self.serializer_class.loader_options(self.model_class)
        for include_path in self.query_policy.includes:
            self.serializer_class.loader_options(self.model_class, include=(include_path,))
        self._register_resource_routes()
        self._register_relationship_routes()

    def _register_resource_routes(self) -> None:
        self.router.add_api_route(
            "",
            self._index_delegate,
            methods=["GET"],
            response_class=JsonApiResponse,
            response_model=SuccessDocument,
            responses=_jsonapi_error_responses(400, 406, 422, 500),
            name=f"{type(self).__name__}.index",
        )
        self.router.add_api_route(
            "",
            self._create_delegate(),
            methods=["POST"],
            response_class=JsonApiResponse,
            response_model=SuccessDocument,
            status_code=201,
            responses=_jsonapi_error_responses(400, 403, 406, 409, 415, 422, 500),
            name=f"{type(self).__name__}.create",
        )
        self.router.add_api_route(
            "/{resource_id}",
            self._show_delegate,
            methods=["GET"],
            response_class=JsonApiResponse,
            response_model=SuccessDocument,
            responses=_jsonapi_error_responses(400, 404, 406, 422, 500),
            name=f"{type(self).__name__}.show",
        )
        self.router.add_api_route(
            "/{resource_id}",
            self._update_delegate(),
            methods=["PATCH"],
            response_class=JsonApiResponse,
            response_model=SuccessDocument,
            responses=_jsonapi_error_responses(400, 404, 406, 409, 415, 422, 500),
            name=f"{type(self).__name__}.update",
        )
        if self.enable_upsert:
            self.router.add_api_route(
                "/{resource_id}",
                self._upsert_delegate(),
                methods=["PUT"],
                response_class=JsonApiResponse,
                response_model=SuccessDocument,
                responses={
                    201: {
                        "description": "Resource created",
                        "model": SuccessDocument,
                        "headers": {
                            "Location": {
                                "description": "Canonical URL of the created resource",
                                "schema": {"type": "string"},
                            }
                        },
                    },
                    **_jsonapi_error_responses(400, 404, 406, 409, 415, 422, 500),
                },
                name=f"{type(self).__name__}.upsert",
            )
        self.router.add_api_route(
            "/{resource_id}",
            self._destroy_delegate,
            methods=["DELETE"],
            status_code=204,
            response_class=JsonApiResponse,
            responses=_jsonapi_error_responses(400, 404, 406, 422, 500),
            name=f"{type(self).__name__}.destroy",
        )

    def _register_relationship_routes(self) -> None:
        for public_name, definition in self.serializer_class.relationships.items():
            if not public_name or "/" in public_name or "{" in public_name or "}" in public_name:
                raise ValueError(f"invalid public relationship route name {public_name!r}")

            relationship_path = f"/{{resource_id}}/relationships/{public_name}"
            self.router.add_api_route(
                relationship_path,
                self._relationship_show_delegate(public_name),
                methods=["GET"],
                response_class=JsonApiResponse,
                response_model=RelationshipDocument,
                responses=_jsonapi_error_responses(400, 404, 406, 422, 500),
                name=f"{type(self).__name__}.relationship.{public_name}.show",
            )
            self.router.add_api_route(
                f"/{{resource_id}}/{public_name}",
                self._related_show_delegate(public_name),
                methods=["GET"],
                response_class=JsonApiResponse,
                response_model=SuccessDocument,
                responses=_jsonapi_error_responses(400, 404, 406, 422, 500),
                name=f"{type(self).__name__}.relationship.{public_name}.related",
            )

            if public_name not in self._writable_relationship_names:
                continue

            mutations: tuple[RelationshipMutation, ...] = (
                ("add", "replace", "remove") if definition.many else ("replace",)
            )
            methods: dict[RelationshipMutation, str] = {
                "add": "POST",
                "replace": "PATCH",
                "remove": "DELETE",
            }
            for mutation in mutations:
                self.router.add_api_route(
                    relationship_path,
                    self._relationship_mutation_delegate(public_name, mutation),
                    methods=[methods[mutation]],
                    status_code=204,
                    response_class=JsonApiResponse,
                    responses=_jsonapi_error_responses(400, 404, 406, 409, 415, 422, 500),
                    name=f"{type(self).__name__}.relationship.{public_name}.{mutation}",
                )

    def _create_delegate(self) -> Callable[..., JsonApiResponse]:
        def create_endpoint(
            request: Request,
            document: BaseModel = _JSONAPI_BODY,
            session: Session = _SESSION_DEPENDENCY,
        ) -> JsonApiResponse:
            return self.create(request, document, session)

        create_endpoint.__name__ = f"{type(self).__name__}_create"
        create_endpoint.__annotations__["document"] = self._create_document_schema
        return create_endpoint

    def _update_delegate(self) -> Callable[..., JsonApiResponse]:
        def update_endpoint(
            resource_id: str,
            request: Request,
            document: BaseModel = _JSONAPI_BODY,
            session: Session = _SESSION_DEPENDENCY,
        ) -> JsonApiResponse:
            return self.update(resource_id, request, document, session)

        update_endpoint.__name__ = f"{type(self).__name__}_update"
        update_endpoint.__annotations__["document"] = self._update_document_schema
        return update_endpoint

    def _upsert_delegate(self) -> Callable[..., JsonApiResponse]:
        def upsert_endpoint(
            resource_id: str,
            request: Request,
            document: BaseModel = _JSONAPI_BODY,
            session: Session = _SESSION_DEPENDENCY,
        ) -> JsonApiResponse:
            return self.upsert(resource_id, request, document, session)

        upsert_endpoint.__name__ = f"{type(self).__name__}_upsert"
        upsert_endpoint.__annotations__["document"] = self._replace_document_schema
        return upsert_endpoint

    def _relationship_show_delegate(self, public_name: str) -> Callable[..., JsonApiResponse]:
        def relationship_show_endpoint(
            resource_id: str,
            request: Request,
            session: Session = _SESSION_DEPENDENCY,
        ) -> JsonApiResponse:
            return self.show_relationship(resource_id, public_name, request, session)

        relationship_show_endpoint.__name__ = f"{type(self).__name__}_{public_name}_relationship_show"
        return relationship_show_endpoint

    def _related_show_delegate(self, public_name: str) -> Callable[..., JsonApiResponse]:
        def related_show_endpoint(
            resource_id: str,
            request: Request,
            session: Session = _SESSION_DEPENDENCY,
        ) -> JsonApiResponse:
            return self.show_related(resource_id, public_name, request, session)

        related_show_endpoint.__name__ = f"{type(self).__name__}_{public_name}_related_show"
        return related_show_endpoint

    def _relationship_mutation_delegate(
        self,
        public_name: str,
        mutation: RelationshipMutation,
    ) -> Callable[..., Response]:
        def relationship_mutation_endpoint(
            resource_id: str,
            request: Request,
            document: BaseModel = _JSONAPI_BODY,
            session: Session = _SESSION_DEPENDENCY,
        ) -> Response:
            return self.mutate_relationship(
                resource_id,
                public_name,
                mutation,
                request,
                document,
                session,
            )

        relationship_mutation_endpoint.__name__ = f"{type(self).__name__}_{public_name}_relationship_{mutation}"
        relationship_mutation_endpoint.__annotations__["document"] = self._relationship_document_schemas[public_name]
        return relationship_mutation_endpoint

    def _index_delegate(
        self,
        request: Request,
        session: Session = _SESSION_DEPENDENCY,
    ) -> JsonApiResponse:
        return self.index(request, session)

    def _show_delegate(
        self,
        resource_id: str,
        request: Request,
        session: Session = _SESSION_DEPENDENCY,
    ) -> JsonApiResponse:
        return self.show(resource_id, request, session)

    def _destroy_delegate(
        self,
        resource_id: str,
        request: Request,
        session: Session = _SESSION_DEPENDENCY,
    ) -> Response:
        return self.destroy(resource_id, request, session)

    def index(self, request: Request, session: Session) -> JsonApiResponse:
        self._bind_session(request, session)
        spec = parse_query(request.query_params, self.query_policy)
        scoped = self.index_scope(select(self.model_class))
        filtered = apply_filters(scoped, spec.filters, self.query_policy)
        count_statement = select(func.count()).select_from(filtered.order_by(None).subquery())
        total = session.scalar(count_statement) or 0

        statement = apply_sort(filtered, spec.sorts, self.query_policy)
        statement = statement.options(*self.serializer_class.loader_options(self.model_class, spec.includes))
        statement = apply_pagination(statement, spec.page)
        models = cast(list[ModelT], list(session.scalars(statement).unique().all()))
        document = self.serializer_class.document(models, include=spec.includes)
        if request.query_params.get("include") == "":
            document = document.model_copy(update={"included": []})
        document = document.model_copy(
            update={
                "links": build_pagination_links(
                    request.url.path,
                    request.query_params,
                    spec.page,
                    total=total,
                ),
                "meta": {"totalCount": total},
            }
        )
        return JsonApiResponse(document)

    def show(self, resource_id: str, request: Request, session: Session) -> JsonApiResponse:
        self._bind_session(request, session)
        includes = parse_include_query(request.query_params, self.query_policy)
        model = self._find_resource(session, resource_id, includes=includes)
        document = self.serializer_class.document(model, include=includes)
        if request.query_params.get("include") == "":
            document = document.model_copy(update={"included": []})
        return JsonApiResponse(document)

    def create(
        self,
        request: Request,
        document: BaseModel,
        session: Session,
    ) -> JsonApiResponse:
        self._bind_session(request, session)
        self._reject_query_parameters(request)
        data = self._data(document)
        self._validate_type(data)
        self._reject_client_generated_id(data)
        attributes = cast(CreateT, self._attributes(data))
        relationships = self._relationships(data)
        model = self.model_class()
        self._assign_attributes(model, attributes, exclude_unset=True)
        self.serializer_class.initialize_relationship_defaults(model)

        with session.begin():
            session.add(model)
            with session.no_autoflush:
                self.assign_relationships(session, model, relationships)
                self.before_create(session, model, attributes)
            session.flush()
            self.after_create(session, model, attributes)
            session.flush()
            state = sqlalchemy_inspect(model)
            if state is None:
                raise RuntimeError("created resource is not mapped")
            identity = state.identity
            if not identity:
                raise RuntimeError("created resource does not have a persistent identity")
            response = JsonApiResponse(
                self.serializer_class.document(model),
                status_code=201,
                headers={"Location": self._canonical_resource_location(identity[0])},
            )

        return response

    def update(
        self,
        resource_id: str,
        request: Request,
        document: BaseModel,
        session: Session,
    ) -> JsonApiResponse:
        self._bind_session(request, session)
        self._reject_query_parameters(request)
        data = self._data(document)
        self._validate_type(data)
        self._validate_document_id(data, resource_id)
        relationships = self._relationships(data)
        raw_attributes = getattr(data, "attributes", MISSING)
        if raw_attributes is MISSING and relationships is None:
            raise JsonApiException(
                status_code=422,
                code="VALIDATION_ERROR",
                source_pointer="/data",
            )
        attributes = (
            self.update_schema.model_construct() if raw_attributes is MISSING else cast(UpdateT, self._attributes(data))
        )

        with session.begin():
            model = self._find_resource(session, resource_id, for_update=relationships is not None)
            self.assign_relationships(session, model, relationships)
            self.before_update(session, model, attributes)
            self._assign_attributes(model, attributes, exclude_unset=True)
            session.flush()
            self.after_update(session, model, attributes)
            session.flush()
            response = JsonApiResponse(self.serializer_class.document(model))

        return response

    def upsert(
        self,
        resource_id: str,
        request: Request,
        document: BaseModel,
        session: Session,
    ) -> JsonApiResponse:
        """Atomically create or fully replace one resource using PostgreSQL."""

        self._bind_session(request, session)
        self._reject_query_parameters(request)
        data = self._data(document)
        self._validate_type(data)
        self._validate_document_id(data, resource_id)
        attributes = cast(ReplaceT, self._attributes(data))
        relationships = self._relationships(data)
        coerced_id = self._coerce_resource_id(resource_id)
        mapper = self._model_mapper()
        primary_key = mapper.primary_key[0]

        with session.begin():
            self._lock_upsert_resource(session, coerced_id)
            existing = self._find_resource_or_none(session, resource_id)
            created = existing is None
            model = existing if existing is not None else self.model_class()
            if created:
                setattr(model, primary_key.key, coerced_id)
                self.serializer_class.initialize_relationship_defaults(model)
                for definition in self.serializer_class.relationships.values():
                    set_committed_value(model, definition.attribute, getattr(model, definition.attribute))

            model_values = dict(self.model_params(attributes, exclude_unset=False))
            with session.no_autoflush:
                for name, value in model_values.items():
                    setattr(model, name, value)
                if created:
                    self.assign_relationships(session, model, relationships)
                else:
                    self._reset_omitted_relationships(model, relationships)
                    self.assign_relationships(session, model, relationships)
                self.before_upsert(session, model, attributes)

                relationships_to_reapply = self._relationships_to_reapply(model, relationships) if created else {}
                if created:
                    self._clear_transient_relationship_backrefs(model, relationships_to_reapply)

                state = sqlalchemy_inspect(model)
                if state is None:
                    raise RuntimeError("upsert resource is not mapped")
                persisted_names = set(model_values)
                persisted_names.update(
                    attribute.key
                    for attribute in mapper.column_attrs
                    if attribute.key != primary_key.key and state.attrs[attribute.key].history.has_changes()
                )
                persisted_values = {name: getattr(model, name) for name in persisted_names}
                insert_statement = postgresql_insert(mapper.local_table).values(
                    **{primary_key.key: coerced_id, **persisted_values}
                )
                update_values: dict[str, object] = dict(persisted_values)
                if "updated_at" in mapper.local_table.c and "updated_at" not in update_values:
                    update_values["updated_at"] = func.now()
                upsert_statement = insert_statement.on_conflict_do_update(
                    index_elements=[primary_key],
                    set_=update_values,
                )
                returned = session.execute(upsert_statement.returning(*mapper.local_table.c)).mappings().one()

            if created:
                model = self._find_resource(session, resource_id)
                for public_name, value in relationships_to_reapply.items():
                    definition = self._relationship_definition(public_name)
                    setattr(model, definition.attribute, value)
            else:
                for name in persisted_values:
                    set_committed_value(model, name, returned[name])
                if "updated_at" in returned:
                    set_committed_value(model, "updated_at", returned["updated_at"])

            self.after_upsert(session, model, attributes)
            session.flush()
            identifier = self._model_identity(model)
            headers = {"Location": self._canonical_resource_location(identifier)} if created else None
            response = JsonApiResponse(
                self.serializer_class.document(model),
                status_code=201 if created else 200,
                headers=headers,
            )

        return response

    def destroy(self, resource_id: str, request: Request, session: Session) -> Response:
        self._bind_session(request, session)
        self._reject_query_parameters(request)
        with session.begin():
            model = self._find_resource(session, resource_id)
            self.before_destroy(session, model)
            session.delete(model)
            session.flush()
            self.after_destroy(session, model)
        return Response(status_code=204)

    def show_relationship(
        self,
        resource_id: str,
        public_name: str,
        request: Request,
        session: Session,
    ) -> JsonApiResponse:
        """Return linkage for one serializer-declared relationship."""

        self._bind_session(request, session)
        self._reject_query_parameters(request)
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
        """Return the resource document addressed by a related-resource URL."""

        self._bind_session(request, session)
        self._reject_query_parameters(request)
        definition = self._relationship_definition(public_name)
        model = self._find_resource(session, resource_id)
        related = getattr(model, definition.attribute)
        return JsonApiResponse(definition.serializer.document(related))

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

        self._bind_session(request, session)
        self._reject_query_parameters(request)
        definition = self._relationship_definition(public_name)
        if not definition.many and mutation != "replace":
            raise JsonApiException(status_code=400, code="INVALID_JSONAPI_DOCUMENT")
        linkage = self._linkage_data(document)

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

    def index_scope(self, statement: Select[Any]) -> Select[Any]:
        """Return the collection scope before public filters are applied."""

        return statement

    def model_params(
        self,
        attributes: CreateT | UpdateT | ReplaceT,
        *,
        exclude_unset: bool,
    ) -> Mapping[str, object]:
        """Return the strong model parameters assigned by write actions."""

        return attributes.model_dump(
            by_alias=False,
            exclude_unset=exclude_unset,
        )

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
            public_name = field.alias or _snake_to_camel(field_name)
            definition = self.serializer_class.relationships.get(public_name)
            if definition is None:
                raise JsonApiException(
                    status_code=400,
                    code="INVALID_JSONAPI_DOCUMENT",
                    source_pointer=f"/data/relationships/{public_name}",
                )
            relationship_document = getattr(relationships, field_name)
            linkage = self._linkage_data(relationship_document)
            resolved = self._resolve_relationship_data(
                session,
                definition,
                linkage,
                pointer_prefix=f"/data/relationships/{public_name}/data",
            )
            assignments[definition.attribute] = resolved
        return assignments

    def before_create(self, session: Session, model: ModelT, attributes: CreateT) -> None:
        """Run inside the create transaction before persistence."""

    def after_create(self, session: Session, model: ModelT, attributes: CreateT) -> None:
        """Run inside the create transaction after the first flush."""

    def before_update(self, session: Session, model: ModelT, attributes: UpdateT) -> None:
        """Run inside the update transaction before assignment."""

    def after_update(self, session: Session, model: ModelT, attributes: UpdateT) -> None:
        """Run inside the update transaction after the first flush."""

    def before_upsert(self, session: Session, model: ModelT, attributes: ReplaceT) -> None:
        """Run inside the upsert transaction before the PostgreSQL statement."""

    def after_upsert(self, session: Session, model: ModelT, attributes: ReplaceT) -> None:
        """Run inside the upsert transaction after persistence and relationship assignment."""

    def before_destroy(self, session: Session, model: ModelT) -> None:
        """Run inside the destroy transaction before deletion."""

    def after_destroy(self, session: Session, model: ModelT) -> None:
        """Run inside the destroy transaction after the delete flush."""

    def _model_mapper(self) -> Any:
        mapper = sqlalchemy_inspect(self.model_class)
        if mapper is None:
            raise RuntimeError("CRUD model class is not mapped")
        return mapper

    def _lock_upsert_resource(self, session: Session, resource_id: object) -> None:
        bind = session.get_bind()
        if bind.dialect.name != "postgresql":
            raise RuntimeError("CrudActions upsert requires PostgreSQL")
        mapper = self._model_mapper()
        lock_material = f"{mapper.local_table.fullname}:{resource_id}".encode()
        lock_key = int.from_bytes(blake2b(lock_material, digest_size=8).digest(), signed=True)
        session.execute(select(func.pg_advisory_xact_lock(lock_key)))

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
            coerced_id = self._coerce_model_id(
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
            id=str(CrudActions._model_identity(model)),
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

    @staticmethod
    def _linkage_data(document: BaseModel) -> object:
        linkage = getattr(document, "data", MISSING)
        if linkage is MISSING:
            raise JsonApiException(
                status_code=400,
                code="INVALID_JSONAPI_DOCUMENT",
                source_pointer="/data",
            )
        return linkage

    def _relationships_to_reapply(
        self,
        model: ModelT,
        relationships: BaseModel | None,
    ) -> dict[str, object]:
        requested = self._provided_relationship_names(relationships)
        state = sqlalchemy_inspect(model, raiseerr=False)
        if state is None:
            raise RuntimeError("upsert relationship resource is not mapped")
        values: dict[str, object] = {}
        for public_name, definition in self.serializer_class.relationships.items():
            value = getattr(model, definition.attribute)
            changed = state.attrs[definition.attribute].history.has_changes()
            non_default = bool(value) if definition.many else value is not None
            if public_name in requested or changed or non_default:
                values[public_name] = list(value) if definition.many else value
        return values

    def _clear_transient_relationship_backrefs(
        self,
        model: ModelT,
        relationship_values: Mapping[str, object],
    ) -> None:
        """Detach a staged upsert candidate from persistent reverse collections."""

        mapper = self._model_mapper()
        for public_name, value in relationship_values.items():
            definition = self._relationship_definition(public_name)
            relationship = mapper.relationships[definition.attribute]
            reverse_name = relationship.back_populates
            targets = list(cast(Sequence[object], value)) if definition.many else ([] if value is None else [value])
            if reverse_name is not None:
                for target in targets:
                    target_mapper = sqlalchemy_inspect(type(target))
                    if target_mapper is None:
                        raise RuntimeError("relationship target model is not mapped")
                    reverse = target_mapper.relationships[reverse_name]
                    reverse_value = getattr(target, reverse_name)
                    if reverse.uselist:
                        while model in reverse_value:
                            reverse_value.remove(model)
                    elif reverse_value is model:
                        setattr(target, reverse_name, None)
            set_committed_value(model, definition.attribute, value)

    @staticmethod
    def _provided_relationship_names(relationships: BaseModel | None) -> set[str]:
        provided: set[str] = set()
        if relationships is None:
            return provided
        for field_name, field in type(relationships).model_fields.items():
            if field_name in relationships.model_fields_set:
                provided.add(field.alias or _snake_to_camel(field_name))
        return provided

    def _reset_omitted_relationships(
        self,
        model: ModelT,
        relationships: BaseModel | None,
    ) -> None:
        provided = self._provided_relationship_names(relationships)
        for public_name, definition in self.serializer_class.relationships.items():
            if public_name in self._writable_relationship_names and public_name not in provided:
                setattr(model, definition.attribute, [] if definition.many else None)

    def _find_resource_or_none(
        self,
        session: Session,
        resource_id: str,
        *,
        includes: Sequence[str] = (),
        for_update: bool = False,
    ) -> ModelT | None:
        coerced_id = self._coerce_resource_id(resource_id)
        mapper = self._model_mapper()
        primary_key = mapper.primary_key[0]
        statement: Select[Any] = select(self.model_class).where(primary_key == coerced_id)
        if for_update:
            statement = statement.with_for_update()
        statement = statement.options(*self.serializer_class.loader_options(self.model_class, includes))
        return cast(ModelT | None, session.scalars(statement).unique().one_or_none())

    def _find_resource(
        self,
        session: Session,
        resource_id: str,
        *,
        includes: Sequence[str] = (),
        for_update: bool = False,
    ) -> ModelT:
        model = self._find_resource_or_none(
            session,
            resource_id,
            includes=includes,
            for_update=for_update,
        )
        if model is None:
            raise JsonApiException(status_code=404, code="RESOURCE_NOT_FOUND")
        return model

    def _coerce_resource_id(self, resource_id: str) -> object:
        return self._coerce_model_id(self.model_class, resource_id)

    @staticmethod
    def _coerce_model_id(
        model_class: type[Any],
        resource_id: str,
        *,
        relationship: bool = False,
        source_pointer: str | None = None,
    ) -> object:
        mapper = sqlalchemy_inspect(model_class)
        if mapper is None:
            raise RuntimeError("CRUD model class is not mapped")
        primary_key = mapper.primary_key[0]
        try:
            python_type = primary_key.type.python_type
            if python_type is UUID:
                return UUID(resource_id)
            return python_type(resource_id)
        except (AttributeError, TypeError, ValueError):
            if relationship:
                raise JsonApiException(
                    status_code=404,
                    code="RELATIONSHIP_RESOURCE_NOT_FOUND",
                    source_pointer=source_pointer,
                ) from None
            raise JsonApiException(
                status_code=404,
                code="RESOURCE_NOT_FOUND",
                source_pointer=source_pointer,
            ) from None

    def _validate_type(self, data: BaseModel) -> None:
        if getattr(data, "type", None) != self.serializer_class.type_name:
            raise JsonApiException(
                status_code=409,
                code="TYPE_MISMATCH",
                source_pointer="/data/type",
            )

    def _validate_document_id(self, data: BaseModel, resource_id: str) -> None:
        if getattr(data, "id", None) != resource_id:
            raise JsonApiException(
                status_code=409,
                code="ID_MISMATCH",
                source_pointer="/data/id",
            )

    @staticmethod
    def _reject_client_generated_id(data: BaseModel) -> None:
        if getattr(data, "id", MISSING) is not MISSING:
            raise JsonApiException(
                status_code=403,
                code="CLIENT_GENERATED_ID_UNSUPPORTED",
                source_pointer="/data/id",
            )

    def _canonical_resource_location(self, identifier: object) -> str:
        resource_path = self.serializer_class.resource_path or self.prefix
        return f"{resource_path}/{identifier}"

    @staticmethod
    def _data(document: BaseModel) -> BaseModel:
        data = getattr(document, "data", None)
        if not isinstance(data, BaseModel):
            raise JsonApiException(status_code=400, code="INVALID_JSONAPI_DOCUMENT")
        return data

    @staticmethod
    def _attributes(data: BaseModel) -> BaseModel:
        attributes = getattr(data, "attributes", None)
        if not isinstance(attributes, BaseModel):
            raise JsonApiException(status_code=400, code="INVALID_JSONAPI_DOCUMENT")
        return attributes

    @staticmethod
    def _relationships(data: BaseModel) -> BaseModel | None:
        relationships = getattr(data, "relationships", None)
        if relationships is MISSING or relationships is None:
            return None
        if not isinstance(relationships, BaseModel):
            raise JsonApiException(
                status_code=400,
                code="INVALID_JSONAPI_DOCUMENT",
                source_pointer="/data/relationships",
            )
        return relationships

    def _assign_attributes(
        self,
        model: ModelT,
        attributes: CreateT | UpdateT | ReplaceT,
        *,
        exclude_unset: bool,
    ) -> None:
        values = self.model_params(attributes, exclude_unset=exclude_unset)
        for name, value in values.items():
            setattr(model, name, value)

    @staticmethod
    def _reject_query_parameters(request: Request) -> None:
        first_item = next(iter(request.query_params.multi_items()), None)
        if first_item is not None:
            raise JsonApiException(
                status_code=400,
                code="INVALID_QUERY_PARAMETER",
                source_parameter=first_item[0],
            )

    @staticmethod
    def _bind_session(request: Request, session: Session) -> None:
        request.state.session = session

"""Declaration surface, lifecycle hooks, and single-resource lookup for CRUD controllers.

This is the root of the ``CrudActions`` inheritance chain
(``CrudDeclarations`` -> ``CrudRelationships`` -> ``CrudUpsert`` -> ``CrudActions``).
Everything a template user is meant to declare or override lives here, next to the few
lookup helpers every layer above needs. The chain only ever references downwards, so no
layer depends on a member defined above it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import Select, select
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.orm import Session

from app.controllers.concerns.document_parsing import coerce_model_id
from app.controllers.concerns.jsonapi_controller import JsonApiController
from app.jsonapi import JsonApiException
from app.jsonapi.query import QueryPolicy
from app.serializers.base import JsonApiSerializer


class CrudDeclarations[
    ModelT,
    CreateT: BaseModel,
    UpdateT: BaseModel,
    ReplaceT: BaseModel,
](JsonApiController):
    """Hold the declaration contract, the overridable hooks, and resource lookup."""

    model_class: type[ModelT]
    serializer_class: type[JsonApiSerializer[ModelT]]
    create_schema: type[CreateT]
    update_schema: type[UpdateT]
    replace_schema: type[ReplaceT]
    relationships_schema: type[BaseModel] | None = None
    query_policy: QueryPolicy
    enable_upsert = False
    read_dependencies: tuple[Callable[..., Any], ...] = ()
    write_dependencies: tuple[Callable[..., Any], ...] = ()

    # Built once by ``CrudActions.__init__`` and read by the layers above.
    _create_document_schema: type[BaseModel]
    _update_document_schema: type[BaseModel]
    _replace_document_schema: type[BaseModel]
    _writable_relationship_names: frozenset[str]
    _relationship_document_schemas: Mapping[str, type[BaseModel]]

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

    def _find_resource_or_none(
        self,
        session: Session,
        resource_id: str,
        *,
        includes: Sequence[str] = (),
        for_update: bool = False,
        linkage_only: bool = False,
    ) -> ModelT | None:
        coerced_id = self._coerce_resource_id(resource_id)
        mapper = self._model_mapper()
        primary_key = mapper.primary_key[0]
        statement: Select[Any] = select(self.model_class).where(primary_key == coerced_id)
        if for_update:
            statement = statement.with_for_update()
        statement = statement.options(
            *self.serializer_class.loader_options(self.model_class, includes, linkage_only=linkage_only)
        )
        return cast(ModelT | None, session.scalars(statement).unique().one_or_none())

    def _find_resource(
        self,
        session: Session,
        resource_id: str,
        *,
        includes: Sequence[str] = (),
        for_update: bool = False,
        linkage_only: bool = False,
    ) -> ModelT:
        model = self._find_resource_or_none(
            session,
            resource_id,
            includes=includes,
            for_update=for_update,
            linkage_only=linkage_only,
        )
        if model is None:
            raise JsonApiException(status_code=404, code="RESOURCE_NOT_FOUND")
        return model

    def _require_resource_id(self, session: Session, resource_id: str) -> object:
        """Assert the addressed resource exists without loading it or its relationships."""

        coerced_id = self._coerce_resource_id(resource_id)
        primary_key = self._model_mapper().primary_key[0]
        if session.scalar(select(primary_key).where(primary_key == coerced_id)) is None:
            raise JsonApiException(status_code=404, code="RESOURCE_NOT_FOUND")
        return coerced_id

    def _coerce_resource_id(self, resource_id: str) -> object:
        return coerce_model_id(self.model_class, resource_id)

    def _canonical_resource_location(self, identifier: object) -> str:
        resource_path = self.serializer_class.resource_path or self.prefix
        return f"{resource_path}/{identifier}"

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

"""Rails-style inherited CRUD actions for JSON:API resources.

``CrudActions`` is the single public class of this package's CRUD chain and stays the
only import a resource controller needs. It assembles the per-controller write document
schemas, registers the routes, and holds the collection and single-resource actions; the
rest of the behaviour is inherited, in this order:

``CrudDeclarations`` (:mod:`~app.controllers.concerns.crud_base`)
    declaration contract, ``index_scope``/``model_params``, the eight ``before_*``/
    ``after_*`` hooks, and single-resource lookup.
``CrudRelationships`` (:mod:`~app.controllers.concerns.relationship_resolver`)
    the three relationship actions, ``assign_relationships``, and linkage resolution.
``CrudUpsert`` (:mod:`~app.controllers.concerns.upsert_executor`)
    the PostgreSQL ``upsert`` action.

Route and OpenAPI assembly lives in :mod:`~app.controllers.concerns.route_registrar`,
and stateless document parsing in :mod:`~app.controllers.concerns.document_parsing`.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import Request
from pydantic import BaseModel
from pydantic.experimental.missing_sentinel import MISSING
from sqlalchemy import func, select
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.controllers.concerns.document_parsing import (
    document_attributes,
    document_data,
    document_relationships,
    reject_client_generated_id,
    reject_query_parameters,
    require_document_id,
    require_resource_type,
)
from app.controllers.concerns.jsonapi_routes import (
    relationship_document_model,
    write_document_model,
)
from app.controllers.concerns.route_registrar import (
    RelationshipMutation,
    register_relationship_routes,
    register_resource_routes,
)
from app.controllers.concerns.upsert_executor import CrudUpsert
from app.jsonapi import JsonApiException, JsonApiResponse
from app.jsonapi.naming import snake_to_camel
from app.jsonapi.query import (
    apply_filters,
    apply_keyset,
    apply_pagination,
    apply_sort,
    build_pagination_links,
    encode_cursor,
    keyset_sorts,
    parse_include_query,
    parse_query,
)

__all__ = ["CrudActions", "RelationshipMutation"]


class CrudActions[
    ModelT,
    CreateT: BaseModel,
    UpdateT: BaseModel,
    ReplaceT: BaseModel,
](CrudUpsert[ModelT, CreateT, UpdateT, ReplaceT]):
    """Reusable CRUD controller with overridable Rails-like lifecycle hooks."""

    def __init__(self, *, prefix: str, tags: list[str]) -> None:
        super().__init__(prefix=prefix, tags=tags)
        self._create_document_schema = None
        self._update_document_schema = None
        self._replace_document_schema = None
        if self.enable_writes:
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
        # 읽기 전용 자원은 relationships_schema를 선언했더라도 쓰기 관계 라우트를
        # 갖지 않는다. 이 빈 집합이 register_relationship_routes의 mutation 등록을
        # 막는 유일한 장치이므로 조건을 여기서 건다.
        self._writable_relationship_names = (
            frozenset(
                field.alias or snake_to_camel(field_name)
                for field_name, field in (
                    self.relationships_schema.model_fields.items() if self.relationships_schema is not None else ()
                )
            )
            if self.enable_writes
            else frozenset()
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
        self.serializer_class.loader_options(self.model_class, linkage_only=True)
        for include_path in self.query_policy.includes:
            self.serializer_class.loader_options(self.model_class, include=(include_path,))
            self.serializer_class.loader_options(self.model_class, include=(include_path,), linkage_only=True)
        register_resource_routes(
            self.router,
            controller_name=type(self).__name__,
            read_dependencies=self.read_dependencies,
            write_dependencies=self.write_dependencies,
            enable_writes=self.enable_writes,
            enable_upsert=self.enable_upsert,
            index=self.index,
            show=self.show,
            create=self.create,
            update=self.update,
            upsert=self.upsert,
            destroy=self.destroy,
            create_document_schema=self._create_document_schema,
            update_document_schema=self._update_document_schema,
            replace_document_schema=self._replace_document_schema,
        )
        register_relationship_routes(
            self.router,
            controller_name=type(self).__name__,
            relationships=self.serializer_class.relationships,
            writable_names=self._writable_relationship_names,
            relationship_document_schemas=self._relationship_document_schemas,
            read_dependencies=self.read_dependencies,
            write_dependencies=self.write_dependencies,
            show_relationship=self.show_relationship,
            show_related=self.show_related,
            mutate_relationship=self.mutate_relationship,
        )

    def index(self, request: Request, session: Session) -> JsonApiResponse:
        """Return one page of the collection without paying for a COUNT by default.

        ``meta.totalCount`` and a non-null ``links.last`` are opt-in through
        ``page[totals]=true``; the ``next`` link is decided by one probe row fetched
        alongside the page instead. ``page[after]``/``page[before]`` switch the page to a
        keyset window over the same effective sort, which drops the OFFSET entirely.

        ``LIMIT size + 1`` is applied by the database to raw rows, so the probe has to be
        read before the identity de-duplication that ``index_scope`` may make necessary.
        Deciding ``has_more`` from the de-duplicated list instead would end the walk early
        whenever a scope multiplies rows, stranding the rest of the collection.
        """

        spec = parse_query(request.query_params, self.query_policy)
        page = spec.page
        cursor = page.cursor
        scoped = self.index_scope(select(self.model_class))
        filtered = apply_filters(scoped, spec.filters, self.query_policy)

        total: int | None = None
        if page.totals:
            count_statement = select(func.count()).select_from(filtered.order_by(None).subquery())
            total = session.scalar(count_statement) or 0

        if cursor is not None:
            filtered = apply_keyset(filtered, spec.sorts, self.query_policy, cursor)
        statement = apply_sort(filtered, keyset_sorts(spec.sorts, cursor), self.query_policy)
        statement = statement.options(
            *self.serializer_class.loader_options(self.model_class, spec.includes, linkage_only=True)
        )
        statement = apply_pagination(statement, page, probe=True)
        raw_rows = cast(list[ModelT], list(session.scalars(statement)))
        has_more = len(raw_rows) > page.size
        rows = list(dict.fromkeys(raw_rows))
        models = rows[: page.size]
        if cursor is not None and cursor.before:
            models.reverse()

        document = self.serializer_class.document(models, include=spec.includes)
        if request.query_params.get("include") == "":
            document = document.model_copy(update={"included": []})
        update: dict[str, Any] = {
            "links": build_pagination_links(
                request.url.path,
                request.query_params,
                page,
                total=total,
                has_more=has_more,
                next_cursor=encode_cursor(models[-1], spec.sorts, self.query_policy) if models else None,
                prev_cursor=encode_cursor(models[0], spec.sorts, self.query_policy) if models else None,
            )
        }
        if total is not None:
            update["meta"] = {"totalCount": total}
        return JsonApiResponse(document.model_copy(update=update))

    def show(self, resource_id: str, request: Request, session: Session) -> JsonApiResponse:
        includes = parse_include_query(request.query_params, self.query_policy)
        model = self._find_resource(session, resource_id, includes=includes, linkage_only=True)
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
        reject_query_parameters(request)
        data = document_data(document)
        require_resource_type(data, expected_type=self.serializer_class.type_name)
        reject_client_generated_id(data)
        attributes = cast(CreateT, document_attributes(data))
        relationships = document_relationships(data)
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
        reject_query_parameters(request)
        data = document_data(document)
        require_resource_type(data, expected_type=self.serializer_class.type_name)
        require_document_id(data, resource_id)
        relationships = document_relationships(data)
        raw_attributes = getattr(data, "attributes", MISSING)
        if raw_attributes is MISSING and relationships is None:
            raise JsonApiException(
                status_code=422,
                code="VALIDATION_ERROR",
                source_pointer="/data",
            )
        attributes = (
            self.update_schema.model_construct()
            if raw_attributes is MISSING
            else cast(UpdateT, document_attributes(data))
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

    def destroy(self, resource_id: str, request: Request, session: Session) -> Response:
        reject_query_parameters(request)
        with session.begin():
            model = self._find_resource(session, resource_id)
            self.before_destroy(session, model)
            session.delete(model)
            session.flush()
            self.after_destroy(session, model)
        return Response(status_code=204)

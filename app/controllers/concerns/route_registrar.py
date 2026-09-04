"""Route registration and FastAPI endpoint delegates for CRUD controllers.

Both registration functions are called once from ``CrudActions.__init__``. The call
order and the order of the ``add_api_route`` calls inside them decide the order of
``paths`` and of the operations inside each path in the generated OpenAPI document,
so neither may be reshuffled. Each route also passes an explicit ``name=``; FastAPI
derives the ``operationId`` from that name, the path and the method, never from the
delegate's ``__name__``.

Why the delegates are built here instead of being plain bound methods: FastAPI reads
the request body model out of the endpoint's signature, and every controller has its
own concrete write-document schema that only exists once ``__init__`` has built it.
The schema is therefore injected into ``__annotations__["document"]`` at runtime,
which is only possible on a freshly created closure. ``__name__`` is rewritten in the
same place so the generated endpoints stay distinguishable in tracebacks.

None of the delegate closures carries a docstring on purpose: FastAPI would publish it
as the operation ``description``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.jsonapi import (
    JSONAPI_MEDIA_TYPE,
    JsonApiResponse,
    RelationshipDocument,
    SuccessDocument,
    jsonapi_error_responses,
)
from app.serializers.base import RelationshipDefinition
from config.database import get_request_session

_JSONAPI_BODY = Body(..., media_type=JSONAPI_MEDIA_TYPE)
_SESSION_DEPENDENCY = Depends(get_request_session)


type RelationshipMutation = Literal["add", "replace", "remove"]

type IndexAction = Callable[[Request, Session], JsonApiResponse]
type ShowAction = Callable[[str, Request, Session], JsonApiResponse]
type CreateAction = Callable[[Request, BaseModel, Session], JsonApiResponse]
type WriteAction = Callable[[str, Request, BaseModel, Session], JsonApiResponse]
type DestroyAction = Callable[[str, Request, Session], Response]
type RelationshipReadAction = Callable[[str, str, Request, Session], JsonApiResponse]
type RelationshipMutationAction = Callable[
    [str, str, RelationshipMutation, Request, BaseModel, Session],
    Response,
]


def register_resource_routes(
    router: APIRouter,
    *,
    controller_name: str,
    read_dependencies: Sequence[Callable[..., Any]],
    write_dependencies: Sequence[Callable[..., Any]],
    enable_writes: bool,
    enable_upsert: bool,
    index: IndexAction,
    show: ShowAction,
    create: CreateAction,
    update: WriteAction,
    upsert: WriteAction,
    destroy: DestroyAction,
    create_document_schema: type[BaseModel] | None,
    update_document_schema: type[BaseModel] | None,
    replace_document_schema: type[BaseModel] | None,
) -> None:
    """Register the collection and single-resource routes in document order."""

    router.add_api_route(
        "",
        _index_delegate(controller_name, index),
        methods=["GET"],
        response_class=JsonApiResponse,
        response_model=SuccessDocument,
        responses=jsonapi_error_responses(400, 406, 422, 500),
        dependencies=[Depends(dependency) for dependency in read_dependencies],
        name=f"{controller_name}.index",
    )
    # POST는 GET ""과 GET "/{resource_id}" 사이에 있어야 한다 — 이 순서가
    # OpenAPI 문서의 operation 순서를 정하므로 재배치하지 않는다.
    #
    # 스키마 None 검사를 assert가 아니라 raise로 쓰는 이유: `app/`에는 assert 선례가
    # 없고, `python -O`가 assert를 지운다. 검사 자체는 mypy strict가 요구한다 —
    # 인자가 `| None`이므로 좁히지 않으면 델리게이트에 넘길 수 없다.
    if enable_writes:
        if create_document_schema is None:
            raise ValueError("write routes require a create document schema")
        router.add_api_route(
            "",
            _create_delegate(controller_name, create, create_document_schema),
            methods=["POST"],
            response_class=JsonApiResponse,
            response_model=SuccessDocument,
            status_code=201,
            responses=_write_error_responses(write_dependencies, 400, 403, 406, 409, 415, 422, 500),
            dependencies=[Depends(dependency) for dependency in write_dependencies],
            name=f"{controller_name}.create",
        )
    router.add_api_route(
        "/{resource_id}",
        _show_delegate(controller_name, show),
        methods=["GET"],
        response_class=JsonApiResponse,
        response_model=SuccessDocument,
        responses=jsonapi_error_responses(400, 404, 406, 422, 500),
        dependencies=[Depends(dependency) for dependency in read_dependencies],
        name=f"{controller_name}.show",
    )
    if enable_writes:
        if update_document_schema is None or replace_document_schema is None:
            raise ValueError("write routes require update and replace document schemas")
        router.add_api_route(
            "/{resource_id}",
            _write_delegate(f"{controller_name}_update", update, update_document_schema),
            methods=["PATCH"],
            response_class=JsonApiResponse,
            response_model=SuccessDocument,
            responses=_write_error_responses(write_dependencies, 400, 404, 406, 409, 415, 422, 500),
            dependencies=[Depends(dependency) for dependency in write_dependencies],
            name=f"{controller_name}.update",
        )
        if enable_upsert:
            router.add_api_route(
                "/{resource_id}",
                _write_delegate(f"{controller_name}_upsert", upsert, replace_document_schema),
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
                    **_write_error_responses(write_dependencies, 400, 404, 406, 409, 415, 422, 500),
                },
                dependencies=[Depends(dependency) for dependency in write_dependencies],
                name=f"{controller_name}.upsert",
            )
        router.add_api_route(
            "/{resource_id}",
            _destroy_delegate(controller_name, destroy),
            methods=["DELETE"],
            status_code=204,
            response_class=JsonApiResponse,
            responses=_write_error_responses(write_dependencies, 400, 404, 406, 422, 500),
            dependencies=[Depends(dependency) for dependency in write_dependencies],
            name=f"{controller_name}.destroy",
        )


def register_relationship_routes(
    router: APIRouter,
    *,
    controller_name: str,
    relationships: Mapping[str, RelationshipDefinition],
    writable_names: frozenset[str],
    relationship_document_schemas: Mapping[str, type[BaseModel]],
    read_dependencies: Sequence[Callable[..., Any]],
    write_dependencies: Sequence[Callable[..., Any]],
    show_relationship: RelationshipReadAction,
    show_related: RelationshipReadAction,
    mutate_relationship: RelationshipMutationAction,
) -> None:
    """Register linkage and related-resource routes for every declared relationship."""

    for public_name, definition in relationships.items():
        if not public_name or "/" in public_name or "{" in public_name or "}" in public_name:
            raise ValueError(f"invalid public relationship route name {public_name!r}")

        relationship_path = f"/{{resource_id}}/relationships/{public_name}"
        router.add_api_route(
            relationship_path,
            _relationship_read_delegate(
                f"{controller_name}_{public_name}_relationship_show",
                public_name,
                show_relationship,
            ),
            methods=["GET"],
            response_class=JsonApiResponse,
            response_model=RelationshipDocument,
            responses=jsonapi_error_responses(400, 404, 406, 422, 500),
            dependencies=[Depends(dependency) for dependency in read_dependencies],
            name=f"{controller_name}.relationship.{public_name}.show",
        )
        router.add_api_route(
            f"/{{resource_id}}/{public_name}",
            _relationship_read_delegate(
                f"{controller_name}_{public_name}_related_show",
                public_name,
                show_related,
            ),
            methods=["GET"],
            response_class=JsonApiResponse,
            response_model=SuccessDocument,
            responses=jsonapi_error_responses(400, 404, 406, 422, 500),
            dependencies=[Depends(dependency) for dependency in read_dependencies],
            name=f"{controller_name}.relationship.{public_name}.related",
        )

        if public_name not in writable_names:
            continue

        mutations: tuple[RelationshipMutation, ...] = ("add", "replace", "remove") if definition.many else ("replace",)
        methods: dict[RelationshipMutation, str] = {
            "add": "POST",
            "replace": "PATCH",
            "remove": "DELETE",
        }
        for mutation in mutations:
            router.add_api_route(
                relationship_path,
                _relationship_mutation_delegate(
                    f"{controller_name}_{public_name}_relationship_{mutation}",
                    public_name,
                    mutation,
                    mutate_relationship,
                    relationship_document_schemas[public_name],
                ),
                methods=[methods[mutation]],
                status_code=204,
                response_class=JsonApiResponse,
                responses=_write_error_responses(write_dependencies, 400, 404, 406, 409, 415, 422, 500),
                dependencies=[Depends(dependency) for dependency in write_dependencies],
                name=f"{controller_name}.relationship.{public_name}.{mutation}",
            )


def _write_error_responses(
    write_dependencies: Sequence[Callable[..., Any]],
    *status_codes: int,
) -> dict[int | str, dict[str, Any]]:
    """Add the authentication failures a guarded write route can also answer with."""

    if write_dependencies:
        status_codes = tuple(dict.fromkeys((401, 403, *status_codes)))
    return jsonapi_error_responses(*status_codes)


def _index_delegate(controller_name: str, index: IndexAction) -> Callable[..., JsonApiResponse]:
    def index_endpoint(
        request: Request,
        session: Session = _SESSION_DEPENDENCY,
    ) -> JsonApiResponse:
        return index(request, session)

    index_endpoint.__name__ = f"{controller_name}_index"
    return index_endpoint


def _show_delegate(controller_name: str, show: ShowAction) -> Callable[..., JsonApiResponse]:
    def show_endpoint(
        resource_id: str,
        request: Request,
        session: Session = _SESSION_DEPENDENCY,
    ) -> JsonApiResponse:
        return show(resource_id, request, session)

    show_endpoint.__name__ = f"{controller_name}_show"
    return show_endpoint


def _create_delegate(
    controller_name: str,
    create: CreateAction,
    document_schema: type[BaseModel],
) -> Callable[..., JsonApiResponse]:
    def create_endpoint(
        request: Request,
        document: BaseModel = _JSONAPI_BODY,
        session: Session = _SESSION_DEPENDENCY,
    ) -> JsonApiResponse:
        return create(request, document, session)

    create_endpoint.__name__ = f"{controller_name}_create"
    create_endpoint.__annotations__["document"] = document_schema
    return create_endpoint


def _write_delegate(
    endpoint_name: str,
    write: WriteAction,
    document_schema: type[BaseModel],
) -> Callable[..., JsonApiResponse]:
    def write_endpoint(
        resource_id: str,
        request: Request,
        document: BaseModel = _JSONAPI_BODY,
        session: Session = _SESSION_DEPENDENCY,
    ) -> JsonApiResponse:
        return write(resource_id, request, document, session)

    write_endpoint.__name__ = endpoint_name
    write_endpoint.__annotations__["document"] = document_schema
    return write_endpoint


def _destroy_delegate(controller_name: str, destroy: DestroyAction) -> Callable[..., Response]:
    def destroy_endpoint(
        resource_id: str,
        request: Request,
        session: Session = _SESSION_DEPENDENCY,
    ) -> Response:
        return destroy(resource_id, request, session)

    destroy_endpoint.__name__ = f"{controller_name}_destroy"
    return destroy_endpoint


def _relationship_read_delegate(
    endpoint_name: str,
    public_name: str,
    read: RelationshipReadAction,
) -> Callable[..., JsonApiResponse]:
    def relationship_read_endpoint(
        resource_id: str,
        request: Request,
        session: Session = _SESSION_DEPENDENCY,
    ) -> JsonApiResponse:
        return read(resource_id, public_name, request, session)

    relationship_read_endpoint.__name__ = endpoint_name
    return relationship_read_endpoint


def _relationship_mutation_delegate(
    endpoint_name: str,
    public_name: str,
    mutation: RelationshipMutation,
    mutate: RelationshipMutationAction,
    document_schema: type[BaseModel],
) -> Callable[..., Response]:
    def relationship_mutation_endpoint(
        resource_id: str,
        request: Request,
        document: BaseModel = _JSONAPI_BODY,
        session: Session = _SESSION_DEPENDENCY,
    ) -> Response:
        return mutate(resource_id, public_name, mutation, request, document, session)

    relationship_mutation_endpoint.__name__ = endpoint_name
    relationship_mutation_endpoint.__annotations__["document"] = document_schema
    return relationship_mutation_endpoint

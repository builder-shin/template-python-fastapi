"""Private FastAPI route and request-document builders for CRUD controllers."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, create_model
from pydantic.experimental.missing_sentinel import MISSING
from starlette.responses import Response

from app.jsonapi import ResourceIdentifier, validate_content_type
from app.jsonapi.naming import WRITE_MODEL_CONFIG


def validate_route_prefix(prefix: str) -> str:
    """Return a router prefix that starts with ``/`` and does not end with one."""

    if not prefix.startswith("/") or prefix.endswith("/"):
        raise ValueError("route prefix must start with '/' and must not end with '/'")
    return prefix


class JsonApiRoute(APIRoute):
    """Validate write media types before FastAPI reads or validates the body."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()
        expects_jsonapi_body = self.body_field is not None

        async def media_type_checked_handler(request: Request) -> Response:
            if expects_jsonapi_body:
                validate_content_type(request.headers.get("content-type"))
            return await original_handler(request)

        return media_type_checked_handler


def write_document_model(
    *,
    name: str,
    attributes_schema: type[BaseModel],
    require_attributes: bool,
    require_id: bool,
    relationships_schema: type[BaseModel] | None,
) -> type[BaseModel]:
    """Build the request document used by create, update, and replace actions."""

    resource_fields: dict[str, Any] = {
        "type": (str, ...),
        "attributes": (attributes_schema, ... if require_attributes else MISSING),
    }
    resource_fields["id"] = (str, ... if require_id else MISSING)
    if relationships_schema is not None:
        resource_fields["relationships"] = (relationships_schema, MISSING)

    resource_model = create_model(
        f"{name}Resource",
        __config__=WRITE_MODEL_CONFIG,
        **resource_fields,
    )
    return create_model(
        f"{name}Document",
        __config__=WRITE_MODEL_CONFIG,
        data=(resource_model, ...),
    )


def relationship_document_model(*, name: str, many: bool) -> type[BaseModel]:
    """Build a relationship linkage request document."""

    data_type: Any = list[ResourceIdentifier] if many else ResourceIdentifier | None
    return create_model(
        f"{name}Document",
        __config__=WRITE_MODEL_CONFIG,
        data=(data_type, ...),
    )

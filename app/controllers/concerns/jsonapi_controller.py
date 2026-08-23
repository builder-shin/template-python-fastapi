"""Shared router assembly rules for every JSON:API controller."""

from __future__ import annotations

from enum import Enum
from typing import ClassVar, cast

from fastapi import APIRouter, Depends

from app.controllers.concerns.jsonapi_routes import JsonApiRoute, validate_route_prefix
from app.jsonapi import require_jsonapi_accept

_ACCEPT_DEPENDENCY = Depends(require_jsonapi_accept)


class JsonApiController:
    """Own the router every JSON:API controller registers its routes on.

    Prefix validation, ``Accept`` negotiation and write ``Content-Type``
    validation are router-level keyword arguments that neither mypy nor ruff can
    require, so a controller that hand-assembles its own ``APIRouter`` can drop
    one of them silently. Controllers therefore inherit this base instead of
    repeating the assembly: resource controllers reach it through ``CrudActions``
    and non-CRUD controllers subclass it directly.

    A controller that deliberately serves outside the ``Accept`` contract sets
    ``negotiate_accept = False`` so the omission is a declaration rather than an
    oversight; ``JsonApiRoute`` still validates write media types on that router.

    Mounting at the application root is declared the same way. An empty prefix is
    only legal for a controller that sets ``allow_root_prefix = True`` - such as
    ``HealthController``, which registers absolute paths - so a controller that
    reaches the root by a missing or mistyped prefix still fails loudly instead of
    silently serving from ``/``.
    """

    negotiate_accept: ClassVar[bool] = True
    allow_root_prefix: ClassVar[bool] = False

    def __init__(self, *, prefix: str = "", tags: list[str]) -> None:
        self.prefix = prefix if prefix == "" and self.allow_root_prefix else validate_route_prefix(prefix)
        self.router = APIRouter(
            prefix=self.prefix,
            tags=cast(list[str | Enum], tags),
            dependencies=[_ACCEPT_DEPENDENCY] if self.negotiate_accept else [],
            route_class=JsonApiRoute,
        )

"""Current authenticated user JSON:API controller."""

from __future__ import annotations

from enum import Enum
from typing import Any, cast

from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_user
from app.controllers.concerns.jsonapi_routes import JsonApiRoute
from app.jsonapi import ErrorDocument, JsonApiResponse, SuccessDocument, require_jsonapi_accept
from app.models import User
from app.serializers import UserSerializer

_CURRENT_USER_DEPENDENCY = Depends(get_current_user)


def _jsonapi_error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {
        status_code: {
            "description": "JSON:API authentication error",
            "model": ErrorDocument,
        }
        for status_code in status_codes
    }


class UsersController:
    """Expose the current authenticated user's public representation."""

    def __init__(self, *, prefix: str, tags: list[str]) -> None:
        if not prefix.startswith("/") or prefix.endswith("/"):
            raise ValueError("users prefix must start with '/' and must not end with '/'")
        self.router = APIRouter(
            prefix=prefix,
            tags=cast(list[str | Enum], tags),
            dependencies=[Depends(require_jsonapi_accept)],
            route_class=JsonApiRoute,
        )
        self.router.add_api_route(
            "/me",
            self.me,
            methods=["GET"],
            response_class=JsonApiResponse,
            response_model=SuccessDocument,
            responses=_jsonapi_error_responses(401, 406, 500),
            name="UsersController.me",
        )

    def me(
        self,
        current_user: User = _CURRENT_USER_DEPENDENCY,
    ) -> JsonApiResponse:
        """Return the public current-user JSON:API document."""

        return JsonApiResponse(UserSerializer.document(current_user))

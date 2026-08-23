"""Current authenticated user JSON:API controller."""

from __future__ import annotations

from fastapi import Depends

from app.auth.dependencies import get_current_user
from app.controllers.concerns import JsonApiController
from app.jsonapi import (
    JsonApiResponse,
    SuccessDocument,
    jsonapi_error_responses,
)
from app.models import User
from app.serializers import UserSerializer

_CURRENT_USER_DEPENDENCY = Depends(get_current_user)


class UsersController(JsonApiController):
    """Expose the current authenticated user's public representation."""

    def __init__(self, *, prefix: str, tags: list[str]) -> None:
        super().__init__(prefix=prefix, tags=tags)
        self.router.add_api_route(
            "/me",
            self.me,
            methods=["GET"],
            response_class=JsonApiResponse,
            response_model=SuccessDocument,
            responses=jsonapi_error_responses(401, 406, 500),
            name="UsersController.me",
        )

    def me(
        self,
        current_user: User = _CURRENT_USER_DEPENDENCY,
    ) -> JsonApiResponse:
        """Return the public current-user JSON:API document."""

        return JsonApiResponse(UserSerializer.document(current_user))

"""JSON:API registration and login controller."""

from __future__ import annotations

from enum import Enum
from typing import Any, cast

from fastapi import APIRouter, Body, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.passwords import DUMMY_PASSWORD_HASH, hash_password, verify_password
from app.auth.refresh_sessions import (
    RefreshSessionError,
    issue_token_pair_for_locked_user,
    lock_user_for_refresh,
    logout_refresh_session,
    rotate_refresh_session,
)
from app.controllers.concerns.jsonapi_routes import JsonApiRoute
from app.jsonapi import (
    JSONAPI_MEDIA_TYPE,
    ErrorDocument,
    JsonApiException,
    JsonApiResponse,
    SuccessDocument,
    require_jsonapi_accept,
)
from app.models import User
from app.schemas import LoginDocument, RefreshTokenDocument, RegisterDocument, normalize_email
from app.serializers import AuthTokenSerializer, UserSerializer
from config.auth import AuthSettings, get_auth_settings
from config.database import get_session

_JSONAPI_BODY = Body(..., media_type=JSONAPI_MEDIA_TYPE)
_SESSION_DEPENDENCY = Depends(get_session)
_AUTH_SETTINGS_DEPENDENCY = Depends(get_auth_settings)


def _jsonapi_error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {
        status_code: {
            "description": "JSON:API error",
            "model": ErrorDocument,
        }
        for status_code in status_codes
    }


def _integrity_constraint_name(error: IntegrityError) -> str | None:
    diagnostics = getattr(error.orig, "diag", None)
    constraint_name = getattr(diagnostics, "constraint_name", None)
    return constraint_name if isinstance(constraint_name, str) else None


class AuthController:
    """Expose registration and credential login through explicit JSON:API routes."""

    def __init__(self, *, prefix: str, tags: list[str]) -> None:
        if not prefix.startswith("/") or prefix.endswith("/"):
            raise ValueError("auth prefix must start with '/' and must not end with '/'")
        self.router = APIRouter(
            prefix=prefix,
            tags=cast(list[str | Enum], tags),
            dependencies=[Depends(require_jsonapi_accept)],
            route_class=JsonApiRoute,
        )
        self.router.add_api_route(
            "/register",
            self.register,
            methods=["POST"],
            response_class=JsonApiResponse,
            response_model=SuccessDocument,
            status_code=201,
            responses=_jsonapi_error_responses(406, 409, 415, 422, 500),
            name="AuthController.register",
        )
        self.router.add_api_route(
            "/login",
            self.login,
            methods=["POST"],
            response_class=JsonApiResponse,
            response_model=SuccessDocument,
            responses=_jsonapi_error_responses(401, 403, 406, 415, 422, 500),
            name="AuthController.login",
        )
        self.router.add_api_route(
            "/refresh",
            self.refresh,
            methods=["POST"],
            response_class=JsonApiResponse,
            response_model=SuccessDocument,
            responses=_jsonapi_error_responses(401, 403, 406, 415, 422, 500),
            name="AuthController.refresh",
        )
        self.router.add_api_route(
            "/logout",
            self.logout,
            methods=["POST"],
            response_class=JsonApiResponse,
            response_model=None,
            status_code=204,
            responses=_jsonapi_error_responses(401, 406, 415, 422, 500),
            name="AuthController.logout",
        )

    def register(
        self,
        request: Request,
        document: RegisterDocument = _JSONAPI_BODY,
        session: Session = _SESSION_DEPENDENCY,
    ) -> JsonApiResponse:
        """Create one normalized local account."""

        request.state.session = session
        try:
            with session.begin():
                attributes = document.data.attributes
                user = User(
                    email=normalize_email(str(attributes.email)),
                    password_hash=hash_password(attributes.password),
                )
                session.add(user)
                session.flush()
                response = JsonApiResponse(
                    UserSerializer.document(user),
                    status_code=201,
                    headers={"Location": "/api/v1/users/me"},
                )
        except IntegrityError as error:
            if _integrity_constraint_name(error) == "uq_users_email":
                raise JsonApiException(
                    status_code=409,
                    code="EMAIL_ALREADY_REGISTERED",
                ) from None
            raise

        return response

    def login(
        self,
        request: Request,
        document: LoginDocument = _JSONAPI_BODY,
        session: Session = _SESSION_DEPENDENCY,
        settings: AuthSettings = _AUTH_SETTINGS_DEPENDENCY,
    ) -> JsonApiResponse:
        """Verify credentials and persist a refresh session atomically."""

        request.state.session = session
        attributes = document.data.attributes
        email = normalize_email(str(attributes.email))

        with session.begin():
            user = session.scalar(select(User).where(User.email == email))
            password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
            password_matches = verify_password(attributes.password, password_hash)
            if user is None or not password_matches:
                raise JsonApiException(status_code=401, code="INVALID_CREDENTIALS")
            locked_user = lock_user_for_refresh(session, user.id)
            if locked_user is None:
                raise JsonApiException(status_code=401, code="INVALID_CREDENTIALS")
            if not locked_user.is_active:
                raise JsonApiException(status_code=403, code="USER_INACTIVE")

            token_pair = issue_token_pair_for_locked_user(session, locked_user, settings)
            response = JsonApiResponse(AuthTokenSerializer.document(token_pair))

        return response

    def refresh(
        self,
        request: Request,
        document: RefreshTokenDocument = _JSONAPI_BODY,
        session: Session = _SESSION_DEPENDENCY,
        settings: AuthSettings = _AUTH_SETTINGS_DEPENDENCY,
    ) -> JsonApiResponse:
        """Rotate a valid refresh session in one caller-owned transaction."""

        request.state.session = session
        with session.begin():
            outcome = rotate_refresh_session(
                session,
                document.data.attributes.refresh_token,
                settings,
            )

        if isinstance(outcome, RefreshSessionError):
            raise JsonApiException(status_code=outcome.status_code, code=outcome.code)
        return JsonApiResponse(AuthTokenSerializer.document(outcome))

    def logout(
        self,
        request: Request,
        document: RefreshTokenDocument = _JSONAPI_BODY,
        session: Session = _SESSION_DEPENDENCY,
        settings: AuthSettings = _AUTH_SETTINGS_DEPENDENCY,
    ) -> Response:
        """Revoke one refresh session and return an empty idempotent response."""

        request.state.session = session
        with session.begin():
            outcome = logout_refresh_session(
                session,
                document.data.attributes.refresh_token,
                settings,
            )

        if outcome is not None:
            raise JsonApiException(status_code=outcome.status_code, code=outcome.code)
        return Response(status_code=204)

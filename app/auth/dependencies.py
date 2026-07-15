"""Reusable bearer authentication dependencies."""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.tokens import InvalidToken, TokenExpired, decode_token
from app.jsonapi import JsonApiException
from app.models import User
from config.auth import AuthSettings, get_auth_settings
from config.database import get_auth_session

_BEARER = HTTPBearer(auto_error=False, scheme_name="BearerAuth")
_BEARER_DEPENDENCY = Depends(_BEARER)
_AUTH_SESSION_DEPENDENCY = Depends(get_auth_session)
_AUTH_SETTINGS_DEPENDENCY = Depends(get_auth_settings)


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = _BEARER_DEPENDENCY,
    session: Session = _AUTH_SESSION_DEPENDENCY,
    settings: AuthSettings = _AUTH_SETTINGS_DEPENDENCY,
) -> User:
    """Return the user identified by a strict access-token bearer header."""

    if credentials is None:
        if request.headers.get("Authorization") is None:
            raise JsonApiException(
                status_code=401,
                code="AUTHENTICATION_REQUIRED",
                source_header="Authorization",
            )
        raise JsonApiException(
            status_code=401,
            code="INVALID_TOKEN",
            source_header="Authorization",
        )

    try:
        claims = decode_token(
            credentials.credentials,
            expected_type="access",
            settings=settings,
        )
        user_id = UUID(claims.sub)
    except TokenExpired:
        raise JsonApiException(
            status_code=401,
            code="TOKEN_EXPIRED",
            source_header="Authorization",
        ) from None
    except (InvalidToken, ValueError):
        raise JsonApiException(
            status_code=401,
            code="INVALID_TOKEN",
            source_header="Authorization",
        ) from None

    user = session.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise JsonApiException(
            status_code=401,
            code="INVALID_TOKEN",
            source_header="Authorization",
        )
    return user


_CURRENT_USER_DEPENDENCY = Depends(get_current_user)


def get_current_active_user(
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> User:
    """Return the current user only while the account remains active."""

    if not current_user.is_active:
        raise JsonApiException(status_code=403, code="USER_INACTIVE")
    return current_user

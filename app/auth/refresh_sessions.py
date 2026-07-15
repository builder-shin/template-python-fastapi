"""Refresh-session issuance with caller-owned transactions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from app.auth.tokens import AuthTokenResource, create_token, hash_refresh_token
from app.models import RefreshSession, User
from config.auth import AuthSettings


def issue_token_pair(
    session: Session,
    user: User,
    settings: AuthSettings,
) -> AuthTokenResource:
    """Create and stage one access/refresh pair without committing its session."""

    refresh_jti = uuid4()
    issued_at = datetime.now(UTC).replace(microsecond=0)
    access_token = create_token(
        user.id,
        token_type="access",
        settings=settings,
        now=issued_at,
    )
    refresh_token = create_token(
        user.id,
        token_type="refresh",
        settings=settings,
        jti=refresh_jti,
        now=issued_at,
    )
    refresh_session = RefreshSession(
        id=refresh_jti,
        user_id=user.id,
        token_hash=hash_refresh_token(refresh_token),
        expires_at=issued_at + timedelta(seconds=settings.refresh_expires_seconds),
    )
    session.add(refresh_session)

    return AuthTokenResource(
        id=refresh_jti,
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="Bearer",
        expires_in=settings.access_expires_seconds,
        refresh_expires_in=settings.refresh_expires_seconds,
    )

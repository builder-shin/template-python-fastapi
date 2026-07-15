"""Refresh-session issuance and rotation with caller-owned transactions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth.tokens import (
    AuthTokenResource,
    InvalidToken,
    TokenClaims,
    TokenExpired,
    create_token,
    decode_expired_refresh_token,
    decode_token,
    hash_refresh_token,
    refresh_token_matches,
)
from app.jsonapi import ErrorCode
from app.models import RefreshSession, User
from config.auth import AuthSettings


@dataclass(frozen=True, slots=True)
class RefreshSessionError:
    """Safe error outcome returned after staged security state changes."""

    status_code: int
    code: ErrorCode


def issue_token_pair(
    session: Session,
    user: User,
    settings: AuthSettings,
) -> AuthTokenResource:
    """Lock a persisted user and stage one access/refresh pair."""

    locked_user = lock_user_for_refresh(session, user.id)
    if locked_user is None:
        raise ValueError("user must be persisted before issuing a token pair")
    return issue_token_pair_for_locked_user(session, locked_user, settings)


def issue_token_pair_for_locked_user(
    session: Session,
    locked_user: User,
    settings: AuthSettings,
) -> AuthTokenResource:
    """Stage a token pair after the caller has locked the user row."""

    refresh_jti = uuid4()
    issued_at = datetime.now(UTC).replace(microsecond=0)
    access_token = create_token(
        locked_user.id,
        token_type="access",
        settings=settings,
        now=issued_at,
    )
    refresh_token = create_token(
        locked_user.id,
        token_type="refresh",
        settings=settings,
        jti=refresh_jti,
        now=issued_at,
    )
    refresh_session = RefreshSession(
        id=refresh_jti,
        user_id=locked_user.id,
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


def rotate_refresh_session(
    session: Session,
    raw_token: str,
    settings: AuthSettings,
) -> AuthTokenResource | RefreshSessionError:
    """Rotate one refresh token or stage the required revocation outcome."""

    claims, token_was_expired = _decode_refresh_claims(raw_token, settings)
    if isinstance(claims, RefreshSessionError):
        return claims

    user = _lock_claimed_user(session, claims)
    if user is None:
        return RefreshSessionError(status_code=401, code="INVALID_TOKEN")

    refresh_session = session.scalar(select(RefreshSession).where(RefreshSession.id == claims.jti).with_for_update())
    if refresh_session is None or not _session_matches_claims(
        refresh_session,
        claims,
        raw_token,
    ):
        return RefreshSessionError(status_code=401, code="INVALID_TOKEN")

    now = datetime.now(UTC)
    if token_was_expired or refresh_session.expires_at <= now:
        refresh_session.revoked_at = refresh_session.revoked_at or now
        return RefreshSessionError(status_code=401, code="TOKEN_EXPIRED")

    if refresh_session.revoked_at is not None:
        session.execute(
            update(RefreshSession)
            .where(
                RefreshSession.user_id == refresh_session.user_id,
                RefreshSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        return RefreshSessionError(status_code=401, code="TOKEN_REVOKED")

    if not user.is_active:
        refresh_session.revoked_at = now
        return RefreshSessionError(status_code=403, code="USER_INACTIVE")

    refresh_session.revoked_at = now
    token_pair = issue_token_pair_for_locked_user(session, user, settings)
    session.flush()
    refresh_session.replaced_by_id = token_pair.id
    return token_pair


def logout_refresh_session(
    session: Session,
    raw_token: str,
    settings: AuthSettings,
) -> RefreshSessionError | None:
    """Revoke one valid refresh session while preserving idempotent logout."""

    claims, token_was_expired = _decode_refresh_claims(raw_token, settings)
    if isinstance(claims, RefreshSessionError):
        return claims

    user = _lock_claimed_user(session, claims)
    if user is None:
        return RefreshSessionError(status_code=401, code="INVALID_TOKEN")

    refresh_session = session.scalar(select(RefreshSession).where(RefreshSession.id == claims.jti).with_for_update())
    if refresh_session is None or not _session_matches_claims(
        refresh_session,
        claims,
        raw_token,
    ):
        return RefreshSessionError(status_code=401, code="INVALID_TOKEN")

    now = datetime.now(UTC)
    if token_was_expired or refresh_session.expires_at <= now:
        refresh_session.revoked_at = refresh_session.revoked_at or now
        return RefreshSessionError(status_code=401, code="TOKEN_EXPIRED")

    refresh_session.revoked_at = refresh_session.revoked_at or now
    return None


def _decode_refresh_claims(
    raw_token: str,
    settings: AuthSettings,
) -> tuple[TokenClaims | RefreshSessionError, bool]:
    try:
        return (
            decode_token(raw_token, expected_type="refresh", settings=settings),
            False,
        )
    except TokenExpired:
        try:
            return decode_expired_refresh_token(raw_token, settings=settings), True
        except InvalidToken:
            return RefreshSessionError(status_code=401, code="INVALID_TOKEN"), False
    except InvalidToken:
        return RefreshSessionError(status_code=401, code="INVALID_TOKEN"), False


def _session_matches_claims(
    refresh_session: RefreshSession,
    claims: TokenClaims,
    raw_token: str,
) -> bool:
    return str(refresh_session.user_id) == claims.sub and refresh_token_matches(raw_token, refresh_session.token_hash)


def _lock_claimed_user(session: Session, claims: TokenClaims) -> User | None:
    try:
        user_id = UUID(claims.sub)
    except ValueError:
        return None
    return lock_user_for_refresh(session, user_id)


def lock_user_for_refresh(session: Session, user_id: UUID) -> User | None:
    """Lock and refresh one user before any refresh-session mutation."""

    return session.scalar(
        select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)
    )

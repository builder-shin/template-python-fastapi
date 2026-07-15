"""Refresh-session model persistence tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.models.refresh_session import RefreshSession
from app.models.user import User


def _user(email: str = "sessions@example.com") -> User:
    return User(email=email, password_hash=hash_password("correct horse battery staple"))


def _session(user: User, token_hash: str = "a" * 64) -> RefreshSession:
    return RefreshSession(
        user=user,
        token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )


def test_refresh_session_uses_uuid_jti_and_timezone_timestamps(db_session: Session) -> None:
    refresh_session = _session(_user())
    db_session.add(refresh_session)

    db_session.flush()

    assert isinstance(refresh_session.id, UUID)
    assert refresh_session.id.version == 4
    assert refresh_session.expires_at.tzinfo is not None
    assert refresh_session.created_at.tzinfo is not None
    assert refresh_session.revoked_at is None


def test_refresh_session_requires_a_unique_64_character_token_hash(db_session: Session) -> None:
    user = _user()
    db_session.add_all([_session(user), _session(user)])

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_refresh_session_rejects_a_token_hash_longer_than_64_characters(db_session: Session) -> None:
    refresh_session = _session(_user(), token_hash="a" * 65)
    db_session.add(refresh_session)

    with pytest.raises(DataError):
        db_session.flush()


def test_refresh_session_can_link_to_its_replacement(db_session: Session) -> None:
    user = _user()
    replacement = _session(user, token_hash="b" * 64)
    original = _session(user)
    original.replaced_by = replacement
    original.revoked_at = datetime.now(UTC)
    db_session.add(original)

    db_session.flush()

    assert original.replaced_by_id == replacement.id
    assert original.replaced_by is replacement
    assert original.revoked_at is not None


def test_deleting_user_cascades_refresh_sessions(db_session: Session) -> None:
    user = _user()
    refresh_session = _session(user)
    db_session.add(refresh_session)
    db_session.flush()
    refresh_session_id = refresh_session.id

    db_session.delete(user)
    db_session.flush()

    assert (
        db_session.scalar(
            select(func.count()).select_from(RefreshSession).where(RefreshSession.id == refresh_session_id)
        )
        == 0
    )

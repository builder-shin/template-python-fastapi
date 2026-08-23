"""User model persistence tests."""

from __future__ import annotations

from datetime import UTC
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.models.user import User


def test_user_uses_uuid_v4_active_default_and_timezone_timestamps(db_session: Session) -> None:
    user = User(
        email="person@example.com",
        password_hash=hash_password("correct horse battery staple"),
    )
    db_session.add(user)

    db_session.flush()

    assert isinstance(user.id, UUID)
    assert user.id.version == 4
    assert user.is_active is True
    assert user.created_at.tzinfo is not None
    assert user.created_at.utcoffset() == UTC.utcoffset(user.created_at)
    assert user.updated_at.tzinfo is not None
    assert user.updated_at.utcoffset() == UTC.utcoffset(user.updated_at)


def test_user_requires_a_unique_email(db_session: Session) -> None:
    password_hash = hash_password("correct horse battery staple")
    db_session.add_all(
        [
            User(email="unique@example.com", password_hash=password_hash),
            User(email="unique@example.com", password_hash=password_hash),
        ]
    )

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_user_email_is_limited_to_254_characters(db_session: Session) -> None:
    user = User(email=f"{'a' * 243}@example.com", password_hash=hash_password("valid password value"))
    db_session.add(user)

    with pytest.raises(DataError):
        db_session.flush()


def test_user_requires_a_password_hash(db_session: Session) -> None:
    user = User(email="missing-hash@example.com", password_hash=None)
    db_session.add(user)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_user_persists_normalized_email(db_session: Session) -> None:
    user = User(email="normalized@example.com", password_hash=hash_password("valid password value"))
    db_session.add(user)
    db_session.flush()

    assert db_session.scalar(select(User.email).where(User.id == user.id)) == "normalized@example.com"

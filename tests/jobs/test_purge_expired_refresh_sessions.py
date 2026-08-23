"""Expired refresh-session purge actor tests against real PostgreSQL."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

import app.jobs.refresh_sessions as purge_job
from app.jobs import purge_expired_refresh_sessions
from app.models.refresh_session import RefreshSession
from app.models.user import User

RETENTION_SECONDS = 604_800


@pytest.fixture(autouse=True)
def enable_job_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the test-only logger disabling applied during migration setup."""

    monkeypatch.setattr(purge_job.logger, "disabled", False)


@pytest.fixture(autouse=True)
def default_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the retention window so the job never reads a developer shell value."""

    monkeypatch.setenv("REFRESH_SESSION_RETENTION_SECONDS", str(RETENTION_SECONDS))


@pytest.fixture
def actor_session_factory(
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], Session]:
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr(purge_job, "get_session_factory", lambda: factory)
    return factory


def _user(session: Session, email: str = "purge@example.com") -> User:
    user = User(email=email, password_hash="stored-argon2-digest")
    session.add(user)
    session.flush()
    return user


def _refresh_session(
    session: Session,
    user: User,
    *,
    expires_in: timedelta,
    revoked: bool = False,
) -> RefreshSession:
    now = datetime.now(UTC)
    index = session.scalar(select(func.count()).select_from(RefreshSession)) or 0
    refresh_session = RefreshSession(
        user_id=user.id,
        token_hash=f"{index:064d}",
        expires_at=now + expires_in,
        revoked_at=now if revoked else None,
    )
    session.add(refresh_session)
    session.flush()
    return refresh_session


def _remaining_ids(session: Session) -> set[UUID]:
    session.expire_all()
    return set(session.scalars(select(RefreshSession.id)).all())


def test_actor_uses_production_retry_options() -> None:
    assert purge_expired_refresh_sessions.options["max_retries"] == 3
    assert purge_expired_refresh_sessions.options["min_backoff"] == 15_000


def test_actor_is_exported_for_the_worker_entry_point() -> None:
    import app.jobs as jobs

    assert "purge_expired_refresh_sessions" in jobs.__all__
    assert jobs.purge_expired_refresh_sessions is purge_expired_refresh_sessions


def test_sessions_expired_beyond_retention_are_deleted(
    committed_session: Session,
    actor_session_factory: Callable[[], Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = _user(committed_session)
    _refresh_session(
        committed_session,
        user,
        expires_in=-timedelta(seconds=RETENTION_SECONDS + 3_600),
    )
    _refresh_session(
        committed_session,
        user,
        expires_in=-timedelta(seconds=RETENTION_SECONDS + 7_200),
        revoked=True,
    )
    committed_session.commit()
    caplog.set_level(logging.INFO, logger=purge_job.__name__)

    deleted = purge_expired_refresh_sessions()

    assert deleted == 2
    assert _remaining_ids(committed_session) == set()
    assert committed_session.get(User, user.id) is not None
    record = next(record for record in caplog.records if getattr(record, "event", None) == "refresh_sessions.purged")
    # `deleted` arrives through logging `extra=`, so it is absent from LogRecord stubs.
    assert record.deleted == 2  # type: ignore[attr-defined]


def test_valid_and_recently_revoked_sessions_survive(
    committed_session: Session,
    actor_session_factory: Callable[[], Session],
) -> None:
    user = _user(committed_session)
    valid = _refresh_session(committed_session, user, expires_in=timedelta(days=30))
    revoked_but_unexpired = _refresh_session(
        committed_session,
        user,
        expires_in=timedelta(days=29),
        revoked=True,
    )
    inside_window = _refresh_session(
        committed_session,
        user,
        expires_in=-timedelta(seconds=RETENTION_SECONDS - 3_600),
    )
    committed_session.commit()

    deleted = purge_expired_refresh_sessions()

    assert deleted == 0
    assert _remaining_ids(committed_session) == {
        valid.id,
        revoked_but_unexpired.id,
        inside_window.id,
    }


def test_zero_retention_still_keeps_every_unexpired_session(
    committed_session: Session,
    actor_session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REFRESH_SESSION_RETENTION_SECONDS", "0")
    user = _user(committed_session)
    _refresh_session(committed_session, user, expires_in=-timedelta(seconds=1))
    valid = _refresh_session(committed_session, user, expires_in=timedelta(seconds=1_800))
    committed_session.commit()

    deleted = purge_expired_refresh_sessions()

    assert deleted == 1
    assert _remaining_ids(committed_session) == {valid.id}


def test_a_long_retention_window_deletes_nothing(
    committed_session: Session,
    actor_session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REFRESH_SESSION_RETENTION_SECONDS", str(365 * 24 * 3_600))
    user = _user(committed_session)
    long_expired = _refresh_session(
        committed_session,
        user,
        expires_in=-timedelta(seconds=RETENTION_SECONDS + 3_600),
    )
    committed_session.commit()

    deleted = purge_expired_refresh_sessions()

    assert deleted == 0
    assert _remaining_ids(committed_session) == {long_expired.id}


def test_purge_repeats_until_the_last_batch_is_short(
    committed_session: Session,
    actor_session_factory: Callable[[], Session],
) -> None:
    user = _user(committed_session)
    for offset in range(5):
        _refresh_session(
            committed_session,
            user,
            expires_in=-timedelta(seconds=RETENTION_SECONDS + 3_600 + offset),
        )
    valid = _refresh_session(committed_session, user, expires_in=timedelta(days=30))
    committed_session.commit()

    deleted = purge_expired_refresh_sessions(batch_size=2)

    assert deleted == 5
    assert _remaining_ids(committed_session) == {valid.id}


@pytest.mark.parametrize("batch_size", [0, -1])
def test_non_positive_batch_size_warns_without_opening_a_session(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    batch_size: int,
) -> None:
    def unopened_session_factory() -> Session:
        raise AssertionError("database session must not be opened")

    monkeypatch.setattr(purge_job, "get_session_factory", lambda: unopened_session_factory)
    caplog.set_level(logging.WARNING, logger=purge_job.__name__)

    assert purge_expired_refresh_sessions(batch_size=batch_size) == 0

    warning = next(
        record for record in caplog.records if getattr(record, "event", None) == "refresh_sessions.invalid_batch_size"
    )
    assert warning.batch_size == batch_size  # type: ignore[attr-defined]


def test_purging_an_old_rotation_link_keeps_the_replacement(
    committed_session: Session,
    actor_session_factory: Callable[[], Session],
) -> None:
    user = _user(committed_session)
    replacement = _refresh_session(committed_session, user, expires_in=timedelta(days=30))
    rotated_away = _refresh_session(
        committed_session,
        user,
        expires_in=-timedelta(seconds=RETENTION_SECONDS + 3_600),
        revoked=True,
    )
    rotated_away.replaced_by_id = replacement.id
    committed_session.commit()

    deleted = purge_expired_refresh_sessions()

    assert deleted == 1
    assert _remaining_ids(committed_session) == {replacement.id}


def test_purging_a_whole_rotation_chain_does_not_violate_the_self_reference(
    committed_session: Session,
    actor_session_factory: Callable[[], Session],
) -> None:
    user = _user(committed_session)
    newer = _refresh_session(
        committed_session,
        user,
        expires_in=-timedelta(seconds=RETENTION_SECONDS + 3_600),
    )
    older = _refresh_session(
        committed_session,
        user,
        expires_in=-timedelta(seconds=RETENTION_SECONDS + 7_200),
        revoked=True,
    )
    older.replaced_by_id = newer.id
    committed_session.commit()

    deleted = purge_expired_refresh_sessions()

    assert deleted == 2
    assert _remaining_ids(committed_session) == set()


def test_deleting_only_the_replacement_nulls_the_surviving_link(
    committed_session: Session,
    actor_session_factory: Callable[[], Session],
) -> None:
    user = _user(committed_session)
    replacement = _refresh_session(
        committed_session,
        user,
        expires_in=-timedelta(seconds=RETENTION_SECONDS + 3_600),
    )
    survivor = _refresh_session(committed_session, user, expires_in=timedelta(days=30), revoked=True)
    survivor.replaced_by_id = replacement.id
    committed_session.commit()

    deleted = purge_expired_refresh_sessions()

    assert deleted == 1
    committed_session.expire_all()
    persisted = committed_session.get(RefreshSession, survivor.id)
    assert persisted is not None
    assert persisted.replaced_by_id is None


def test_purge_leaves_other_users_and_their_valid_sessions_untouched(
    committed_session: Session,
    actor_session_factory: Callable[[], Session],
) -> None:
    purged_user = _user(committed_session, email="stale@example.com")
    other_user = _user(committed_session, email="active@example.com")
    _refresh_session(
        committed_session,
        purged_user,
        expires_in=-timedelta(seconds=RETENTION_SECONDS + 3_600),
    )
    other_valid = _refresh_session(committed_session, other_user, expires_in=timedelta(days=30))
    committed_session.commit()

    deleted = purge_expired_refresh_sessions()

    assert deleted == 1
    assert _remaining_ids(committed_session) == {other_valid.id}
    assert set(committed_session.scalars(select(User.id)).all()) == {purged_user.id, other_user.id}


def test_purge_bounds_its_wait_on_the_cascade_skip_locked_does_not_cover(
    concurrent_session_factory: Callable[[], Session],
    actor_session_factory: Callable[[], Session],
) -> None:
    """``SKIP LOCKED`` skips the locked chain row; the cascade walks straight back into it.

    Deleting ``newer`` makes PostgreSQL clear ``older.replaced_by_id`` through the
    ``ON DELETE SET NULL`` self-reference, and that update takes its own row lock with no
    ``SKIP LOCKED`` to fall back on. Without a bound the batch waits forever on the
    ``SELECT ... FOR UPDATE`` rotation holds.
    """

    del actor_session_factory
    with concurrent_session_factory() as setup_session:
        user = _user(setup_session)
        newer = _refresh_session(
            setup_session,
            user,
            expires_in=-timedelta(seconds=RETENTION_SECONDS + 3_600),
        )
        older = _refresh_session(
            setup_session,
            user,
            expires_in=-timedelta(seconds=RETENTION_SECONDS + 7_200),
            revoked=True,
        )
        older.replaced_by_id = newer.id
        setup_session.commit()
        older_id = older.id

    lock_holder = concurrent_session_factory()
    # Exactly the lock rotation and logout take (app/auth/refresh_sessions.py).
    lock_holder.scalars(select(RefreshSession).where(RefreshSession.id == older_id).with_for_update()).one()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(purge_expired_refresh_sessions)
        try:
            error = future.exception(timeout=30)
        finally:
            lock_holder.rollback()
            lock_holder.close()

    assert isinstance(error, OperationalError)
    assert "lock timeout" in str(error).lower()

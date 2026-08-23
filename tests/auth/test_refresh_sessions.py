"""PostgreSQL refresh-session state transition tests."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from queue import Queue
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.auth.refresh_sessions import (
    RefreshSessionError,
    issue_token_pair,
    logout_refresh_session,
    rotate_refresh_session,
)
from app.auth.tokens import AuthTokenResource, create_token, hash_refresh_token
from app.models import RefreshSession, User
from config.auth import AuthSettings

PASSWORD = "correct horse battery staple"  # pragma: allowlist secret
SETTINGS = AuthSettings(secret_key="refresh-session-test-secret-key-at-least-32-bytes")  # pragma: allowlist secret


def _persist_user(session: Session, *, is_active: bool = True) -> User:
    user = User(
        email=f"{uuid4()}@example.com",
        password_hash=hash_password(PASSWORD),
        is_active=is_active,
    )
    session.add(user)
    session.flush()
    return user


def _persist_token(
    session: Session,
    user: User,
    *,
    now: datetime,
    stored_hash: str | None = None,
) -> tuple[str, RefreshSession]:
    jti = uuid4()
    token = create_token(
        user.id,
        token_type="refresh",
        settings=SETTINGS,
        jti=jti,
        now=now,
    )
    row = RefreshSession(
        id=jti,
        user_id=user.id,
        token_hash=stored_hash or hash_refresh_token(token),
        expires_at=now + timedelta(seconds=SETTINGS.refresh_expires_seconds),
    )
    session.add(row)
    session.flush()
    return token, row


def _wait_until_blocked_by(
    session_factory: Callable[[], Session],
    *,
    blocked_pid: int,
    blocker_pid: int,
) -> None:
    deadline = monotonic() + 10
    with session_factory() as observer:
        while monotonic() < deadline:
            blockers = observer.scalar(select(func.pg_blocking_pids(blocked_pid))) or []
            if blocker_pid in blockers:
                return
            sleep(0.01)
    raise AssertionError("refresh transaction did not reach the expected PostgreSQL lock wait")


def test_rotate_stages_one_replacement_and_leaves_transaction_to_caller(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _persist_user(db_session)
    original = issue_token_pair(db_session, user, SETTINGS)
    db_session.flush()

    def forbidden_transaction_method(*args: object, **kwargs: object) -> None:
        raise AssertionError("refresh helper must not own transaction boundaries")

    monkeypatch.setattr(Session, "begin", forbidden_transaction_method)
    monkeypatch.setattr(Session, "commit", forbidden_transaction_method)
    monkeypatch.setattr(Session, "rollback", forbidden_transaction_method)

    outcome = rotate_refresh_session(db_session, original.refresh_token, SETTINGS)
    db_session.flush()

    assert isinstance(outcome, AuthTokenResource)
    old_row = db_session.get(RefreshSession, original.id)
    new_row = db_session.get(RefreshSession, outcome.id)
    assert old_row is not None
    assert old_row.revoked_at is not None
    assert old_row.replaced_by_id == outcome.id
    assert new_row is not None
    assert new_row.revoked_at is None
    assert new_row.token_hash == hash_refresh_token(outcome.refresh_token)
    assert db_session.in_transaction() is True


def test_rotate_marks_only_expired_session_before_returning_error(db_session: Session) -> None:
    user = _persist_user(db_session)
    expired_token, expired_row = _persist_token(
        db_session,
        user,
        now=datetime.now(UTC) - timedelta(days=31),
    )
    active = issue_token_pair(db_session, user, SETTINGS)
    db_session.flush()

    outcome = rotate_refresh_session(db_session, expired_token, SETTINGS)
    db_session.flush()

    assert outcome == RefreshSessionError(status_code=401, code="TOKEN_EXPIRED")
    assert expired_row.revoked_at is not None
    assert db_session.get(RefreshSession, active.id).revoked_at is None  # type: ignore[union-attr]


def test_rotate_rejects_unknown_jti_hash_mismatch_and_wrong_type(db_session: Session) -> None:
    user = _persist_user(db_session)
    now = datetime.now(UTC).replace(microsecond=0)
    unknown = create_token(user.id, token_type="refresh", settings=SETTINGS, now=now)
    mismatched, mismatched_row = _persist_token(
        db_session,
        user,
        now=now,
        stored_hash="0" * 64,
    )
    wrong_type = create_token(user.id, token_type="access", settings=SETTINGS, now=now)

    for raw_token in (unknown, mismatched, wrong_type):
        assert rotate_refresh_session(db_session, raw_token, SETTINGS) == RefreshSessionError(
            status_code=401,
            code="INVALID_TOKEN",
        )

    assert mismatched_row.revoked_at is None


@pytest.mark.parametrize(
    "refresh_operation",
    [rotate_refresh_session, logout_refresh_session],
    ids=["rotate", "logout"],
)
@pytest.mark.parametrize("case", ["unknown_user", "unknown_jti", "hash_mismatch"])
def test_rotate_and_logout_share_one_rejection_path(
    db_session: Session,
    refresh_operation: Callable[[Session, str, AuthSettings], object],
    case: str,
) -> None:
    user = _persist_user(db_session)
    now = datetime.now(UTC).replace(microsecond=0)
    mismatched_row: RefreshSession | None = None
    if case == "unknown_user":
        raw_token = create_token(uuid4(), token_type="refresh", settings=SETTINGS, now=now)
    elif case == "unknown_jti":
        raw_token = create_token(user.id, token_type="refresh", settings=SETTINGS, now=now)
    else:
        raw_token, mismatched_row = _persist_token(db_session, user, now=now, stored_hash="0" * 64)
    db_session.flush()

    assert refresh_operation(db_session, raw_token, SETTINGS) == RefreshSessionError(
        status_code=401,
        code="INVALID_TOKEN",
    )
    if mismatched_row is not None:
        assert mismatched_row.revoked_at is None


def test_logout_is_idempotent_but_expired_token_is_revoked_with_error(db_session: Session) -> None:
    user = _persist_user(db_session)
    active = issue_token_pair(db_session, user, SETTINGS)
    expired_token, expired_row = _persist_token(
        db_session,
        user,
        now=datetime.now(UTC) - timedelta(days=31),
    )
    db_session.flush()

    assert logout_refresh_session(db_session, active.refresh_token, SETTINGS) is None
    assert logout_refresh_session(db_session, active.refresh_token, SETTINGS) is None
    assert db_session.get(RefreshSession, active.id).revoked_at is not None  # type: ignore[union-attr]

    assert logout_refresh_session(db_session, expired_token, SETTINGS) == RefreshSessionError(
        status_code=401,
        code="TOKEN_EXPIRED",
    )
    assert expired_row.revoked_at is not None

    invalid = create_token(user.id, token_type="access", settings=SETTINGS)
    assert logout_refresh_session(db_session, invalid, SETTINGS) == RefreshSessionError(
        status_code=401,
        code="INVALID_TOKEN",
    )


def test_reuse_waits_for_same_user_rotation_then_revokes_its_replacement(
    concurrent_session_factory: Callable[[], Session],
) -> None:
    setup_session = concurrent_session_factory()
    with setup_session.begin():
        user = _persist_user(setup_session)
        reused_token = issue_token_pair(setup_session, user, SETTINGS)
        concurrently_rotated = issue_token_pair(setup_session, user, SETTINGS)
        setup_session.flush()
        first_rotation = rotate_refresh_session(
            setup_session,
            reused_token.refresh_token,
            SETTINGS,
        )
        assert isinstance(first_rotation, AuthTokenResource)
    setup_session.close()

    replacement_staged = Event()
    allow_rotation_commit = Event()
    rotation_pids: Queue[int] = Queue()
    reuse_pids: Queue[int] = Queue()

    def rotate_other_token() -> AuthTokenResource | RefreshSessionError:
        with concurrent_session_factory() as session:
            with session.begin():
                pid = session.scalar(select(func.pg_backend_pid()))
                assert isinstance(pid, int)
                rotation_pids.put(pid)
                outcome = rotate_refresh_session(
                    session,
                    concurrently_rotated.refresh_token,
                    SETTINGS,
                )
                session.flush()
                replacement_staged.set()
                if not allow_rotation_commit.wait(timeout=10):
                    raise TimeoutError("rotation commit permission timed out")
            return outcome

    def reuse_old_token() -> AuthTokenResource | RefreshSessionError:
        if not replacement_staged.wait(timeout=10):
            raise TimeoutError("replacement staging timed out")
        with concurrent_session_factory() as session, session.begin():
            pid = session.scalar(select(func.pg_backend_pid()))
            assert isinstance(pid, int)
            reuse_pids.put(pid)
            return rotate_refresh_session(
                session,
                reused_token.refresh_token,
                SETTINGS,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        rotation_future = executor.submit(rotate_other_token)
        assert replacement_staged.wait(timeout=10)
        reuse_future = executor.submit(reuse_old_token)
        rotation_pid = rotation_pids.get(timeout=10)
        reuse_pid = reuse_pids.get(timeout=10)
        try:
            _wait_until_blocked_by(
                concurrent_session_factory,
                blocked_pid=reuse_pid,
                blocker_pid=rotation_pid,
            )
        finally:
            allow_rotation_commit.set()
        rotation_outcome = rotation_future.result(timeout=10)
        reuse_outcome = reuse_future.result(timeout=10)

    assert isinstance(rotation_outcome, AuthTokenResource)
    assert reuse_outcome == RefreshSessionError(status_code=401, code="TOKEN_REVOKED")
    with concurrent_session_factory() as verification_session:
        rows = list(verification_session.scalars(select(RefreshSession).where(RefreshSession.user_id == user.id)))
        assert len(rows) == 4
        assert all(row.revoked_at is not None for row in rows)


def test_reuse_waits_for_same_user_issuance_then_revokes_issued_session(
    concurrent_session_factory: Callable[[], Session],
) -> None:
    setup_session = concurrent_session_factory()
    with setup_session.begin():
        user = _persist_user(setup_session)
        reused_token = issue_token_pair(setup_session, user, SETTINGS)
        setup_session.flush()
        first_rotation = rotate_refresh_session(
            setup_session,
            reused_token.refresh_token,
            SETTINGS,
        )
        assert isinstance(first_rotation, AuthTokenResource)
    setup_session.close()

    issuance_staged = Event()
    allow_issuance_commit = Event()
    issuance_pids: Queue[int] = Queue()
    reuse_pids: Queue[int] = Queue()

    def issue_another_token() -> AuthTokenResource:
        with concurrent_session_factory() as session:
            with session.begin():
                pid = session.scalar(select(func.pg_backend_pid()))
                assert isinstance(pid, int)
                issuance_pids.put(pid)
                persisted_user = session.get(User, user.id)
                assert persisted_user is not None
                outcome = issue_token_pair(session, persisted_user, SETTINGS)
                session.flush()
                issuance_staged.set()
                if not allow_issuance_commit.wait(timeout=10):
                    raise TimeoutError("issuance commit permission timed out")
            return outcome

    def reuse_old_token() -> AuthTokenResource | RefreshSessionError:
        if not issuance_staged.wait(timeout=10):
            raise TimeoutError("issuance staging timed out")
        with concurrent_session_factory() as session, session.begin():
            pid = session.scalar(select(func.pg_backend_pid()))
            assert isinstance(pid, int)
            reuse_pids.put(pid)
            return rotate_refresh_session(
                session,
                reused_token.refresh_token,
                SETTINGS,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        issuance_future = executor.submit(issue_another_token)
        assert issuance_staged.wait(timeout=10)
        reuse_future = executor.submit(reuse_old_token)
        issuance_pid = issuance_pids.get(timeout=10)
        reuse_pid = reuse_pids.get(timeout=10)
        try:
            _wait_until_blocked_by(
                concurrent_session_factory,
                blocked_pid=reuse_pid,
                blocker_pid=issuance_pid,
            )
        finally:
            allow_issuance_commit.set()
        issuance_outcome = issuance_future.result(timeout=10)
        reuse_outcome = reuse_future.result(timeout=10)

    assert isinstance(issuance_outcome, AuthTokenResource)
    assert reuse_outcome == RefreshSessionError(status_code=401, code="TOKEN_REVOKED")
    with concurrent_session_factory() as verification_session:
        rows = list(verification_session.scalars(select(RefreshSession).where(RefreshSession.user_id == user.id)))
        assert len(rows) == 3
        assert all(row.revoked_at is not None for row in rows)


def test_different_users_rotate_without_waiting_on_each_others_lock(
    concurrent_session_factory: Callable[[], Session],
) -> None:
    setup_session = concurrent_session_factory()
    with setup_session.begin():
        first_user = _persist_user(setup_session)
        first_token = issue_token_pair(setup_session, first_user, SETTINGS)
        second_user = _persist_user(setup_session)
        second_token = issue_token_pair(setup_session, second_user, SETTINGS)
    setup_session.close()

    first_rotation_staged = Event()
    allow_first_commit = Event()

    def hold_first_user_lock() -> AuthTokenResource | RefreshSessionError:
        with concurrent_session_factory() as session:
            with session.begin():
                outcome = rotate_refresh_session(
                    session,
                    first_token.refresh_token,
                    SETTINGS,
                )
                session.flush()
                first_rotation_staged.set()
                if not allow_first_commit.wait(timeout=10):
                    raise TimeoutError("first-user commit permission timed out")
            return outcome

    def rotate_second_user() -> AuthTokenResource | RefreshSessionError:
        if not first_rotation_staged.wait(timeout=10):
            raise TimeoutError("first-user rotation staging timed out")
        with concurrent_session_factory() as session, session.begin():
            return rotate_refresh_session(
                session,
                second_token.refresh_token,
                SETTINGS,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(hold_first_user_lock)
        assert first_rotation_staged.wait(timeout=10)
        second_future = executor.submit(rotate_second_user)
        try:
            second_outcome = second_future.result(timeout=5)
        finally:
            allow_first_commit.set()
        first_outcome = first_future.result(timeout=10)

    assert isinstance(first_outcome, AuthTokenResource)
    assert isinstance(second_outcome, AuthTokenResource)

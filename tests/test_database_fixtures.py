"""Committed and concurrent database fixture contract tests."""

from __future__ import annotations

from collections.abc import Callable
from queue import Queue
from threading import Barrier, Event, Thread

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from app.models import Example, ExampleStatus


def _example_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Example)) or 0


def test_committed_session_makes_commits_visible(
    committed_session: Session,
    db_engine: Engine,
) -> None:
    committed_session.add(Example(title="커밋", status=ExampleStatus.ACTIVE, score=90))
    committed_session.commit()

    with Session(bind=db_engine) as observer:
        assert _example_count(observer) == 1


def test_committed_session_starts_with_clean_tables(committed_session: Session) -> None:
    assert _example_count(committed_session) == 0


def test_committed_session_returns_connections_to_pool(db_engine: Engine) -> None:
    assert isinstance(db_engine.pool, QueuePool)
    assert db_engine.pool.checkedout() == 0


def test_concurrent_session_factory_uses_independent_thread_sessions(
    concurrent_session_factory: Callable[[], Session],
    db_engine: Engine,
) -> None:
    ready = Barrier(2)
    start = Event()
    errors: Queue[BaseException] = Queue()

    def create_example(title: str) -> None:
        try:
            with concurrent_session_factory() as session:
                if not start.wait(timeout=5):
                    raise TimeoutError("thread start event timed out")
                ready.wait(timeout=5)
                session.add(Example(title=title, status=ExampleStatus.DRAFT, score=50))
                session.commit()
        except BaseException as error:
            errors.put(error)

    threads = [
        Thread(target=create_example, args=("동시성-1",), daemon=True),
        Thread(target=create_example, args=("동시성-2",), daemon=True),
    ]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    if not errors.empty():
        error = errors.get_nowait()
        raise AssertionError("concurrent database worker failed") from error

    with Session(bind=db_engine) as observer:
        assert _example_count(observer) == 2

    assert isinstance(db_engine.pool, QueuePool)
    assert db_engine.pool.checkedout() == 0


def test_concurrent_session_factory_cleans_tables_and_pool(db_engine: Engine) -> None:
    with Session(bind=db_engine) as observer:
        assert _example_count(observer) == 0

    assert isinstance(db_engine.pool, QueuePool)
    assert db_engine.pool.checkedout() == 0

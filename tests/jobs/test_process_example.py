"""Example Dramatiq actor tests."""

from __future__ import annotations

import logging
from collections.abc import Callable
from uuid import uuid4

import dramatiq
import pytest
from dramatiq import Worker
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import Retries
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

import app.jobs.example as example_job
from app.jobs import process_example
from app.models.example import Example, ExampleStatus


@pytest.fixture(autouse=True)
def enable_job_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo Alembic's test-only logger disabling after migration setup."""

    monkeypatch.setattr(example_job.logger, "disabled", False)


def _example_state(example: Example) -> tuple[object, ...]:
    return (
        example.id,
        example.title,
        example.description,
        example.status,
        example.score,
        example.category_id,
        example.created_at,
        example.updated_at,
        tuple(tag.id for tag in example.tags),
    )


@pytest.fixture
def actor_session_factory(
    db_engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], Session]:
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr(example_job, "SessionFactory", factory)
    return factory


def test_actor_uses_production_retry_options() -> None:
    assert process_example.options["max_retries"] == 3
    assert process_example.options["min_backoff"] == 15_000


def test_valid_example_logs_success_without_mutating_public_state(
    committed_session: Session,
    actor_session_factory: Callable[[], Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    example = Example(
        title="Worker example",
        description="unchanged",
        status=ExampleStatus.ACTIVE,
        score=80,
    )
    committed_session.add(example)
    committed_session.commit()
    before = _example_state(example)
    caplog.set_level(logging.INFO, logger=example_job.__name__)

    process_example(str(example.id))
    process_example(str(example.id))

    committed_session.expire_all()
    persisted = committed_session.get(Example, example.id)
    assert persisted is not None
    assert _example_state(persisted) == before
    success_records = [record for record in caplog.records if getattr(record, "event", None) == "example.processed"]
    assert len(success_records) == 2
    assert all(record.example_id == str(example.id) for record in success_records)


def test_malformed_uuid_warns_without_opening_database(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session_factory = MagicSessionFactory()
    monkeypatch.setattr(example_job, "SessionFactory", session_factory)
    caplog.set_level(logging.WARNING, logger=example_job.__name__)

    result = process_example("not-a-uuid")

    assert result is None
    assert session_factory.calls == 0
    warning = next(record for record in caplog.records if getattr(record, "event", None) == "example.invalid_id")
    assert warning.example_id == "not-a-uuid"


def test_missing_example_warns_and_returns_successfully(
    actor_session_factory: Callable[[], Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    missing_id = uuid4()
    caplog.set_level(logging.WARNING, logger=example_job.__name__)

    result = process_example(str(missing_id))

    assert result is None
    warning = next(record for record in caplog.records if getattr(record, "event", None) == "example.missing")
    assert warning.example_id == str(missing_id)


def test_database_operational_error_propagates_to_dramatiq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = OperationalError("SELECT examples", {}, RuntimeError("database unavailable"))

    def unavailable_session() -> Session:
        raise error

    monkeypatch.setattr(example_job, "SessionFactory", unavailable_session)

    with pytest.raises(OperationalError) as raised:
        process_example(str(uuid4()))

    assert raised.value is error


def test_worker_retries_operational_error_then_consumes_same_string_message(
    committed_session: Session,
    db_engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example = Example(title="Retry", status=ExampleStatus.DRAFT, score=25)
    committed_session.add(example)
    committed_session.commit()
    real_factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    attempts = 0
    error = OperationalError("SELECT examples", {}, RuntimeError("temporary database failure"))

    def flaky_session_factory() -> Session:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise error
        return real_factory()

    monkeypatch.setattr(example_job, "SessionFactory", flaky_session_factory)
    original_options = dict(process_example.options)
    original_actor_broker = process_example.broker
    original_global_broker = dramatiq.get_broker()
    broker = StubBroker(middleware=[Retries(min_backoff=0, max_backoff=0)])
    worker = Worker(broker, worker_timeout=100)
    worker_threads: list[object] = []
    consumer_threads: list[object] = []

    try:
        process_example.options.update(max_retries=1, min_backoff=0, max_backoff=0)
        process_example.broker = broker
        dramatiq.set_broker(broker)
        broker.declare_actor(process_example)
        broker.emit_after("process_boot")
        worker.start()

        message = process_example.send(str(example.id))
        broker.join(process_example.queue_name, timeout=5_000)
        worker.join()

        assert message.args == (str(example.id),)
        assert attempts == 2
        assert broker.dead_letters == []
    finally:
        worker.stop()
        worker_threads = list(worker.workers)
        consumer_threads = list(worker.consumers.values())
        process_example.options.clear()
        process_example.options.update(original_options)
        process_example.broker = original_actor_broker
        dramatiq.set_broker(original_global_broker)
        original_actor_broker.declare_actor(process_example)

    assert process_example.options == original_options
    assert process_example.broker is original_actor_broker
    assert dramatiq.get_broker() is original_global_broker
    assert all(not thread.is_alive() for thread in [*worker_threads, *consumer_threads])


class MagicSessionFactory:
    """Fail if malformed identifiers try to open a database session."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> Session:
        self.calls += 1
        raise AssertionError("database session must not be opened")

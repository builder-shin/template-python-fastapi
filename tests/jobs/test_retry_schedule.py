"""Exercise production retry middleware without waiting for scheduled deadlines."""

from __future__ import annotations

from unittest.mock import patch

import dramatiq
import pytest
from dramatiq.broker import MessageProxy
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import Retries

from config.broker import configure_broker
from config.retries import SharedRetries


@pytest.mark.parametrize("jitter_high", [False, True])
def test_production_retry_deadlines_and_exhaustion(jitter_high: bool) -> None:
    original_broker = dramatiq.get_broker()
    configured = configure_broker()
    try:
        middleware = next(item for item in configured.middleware if isinstance(item, Retries))
        assert isinstance(middleware, SharedRetries)
        broker = StubBroker(middleware=[middleware])

        @dramatiq.actor(broker=broker, max_retries=3, min_backoff=15_000)
        def retry_target() -> None:
            raise RuntimeError("database unavailable")

        message = MessageProxy(retry_target.message())
        with (
            patch("random.randrange", side_effect=lambda upper: upper - 1 if jitter_high else 0),
            patch.object(broker, "enqueue", wraps=broker.enqueue) as enqueue,
        ):
            for _ in range(4):
                try:
                    retry_target.fn()
                except RuntimeError as error:
                    middleware.after_process_message(broker, message, exception=error)
            assert [call.kwargs["delay"] for call in enqueue.call_args_list] == (
                [24_000, 49_000, 89_000] if jitter_high else [15_000, 30_000, 60_000]
            )
        assert message.failed
        assert message.options["retries"] == 4
        assert "database unavailable" in message.options["traceback"]
    finally:
        configured.close()
        dramatiq.set_broker(original_broker)


def test_explicit_delay_and_declared_non_retryable_errors_preserve_dramatiq_hooks() -> None:
    middleware = SharedRetries()
    broker = StubBroker(middleware=[middleware])

    @dramatiq.actor(broker=broker, throws=ValueError, max_retries=3)
    def target() -> None:
        pass

    with patch.object(broker, "enqueue", wraps=broker.enqueue) as enqueue:
        successful = MessageProxy(target.message())
        middleware.after_process_message(broker, successful)
        assert not successful.failed
        enqueue.assert_not_called()
        rejected = MessageProxy(target.message_with_options(throws=None))
        middleware.after_process_message(broker, rejected, exception=ValueError("invalid argument"))
        assert rejected.failed
        enqueue.assert_not_called()
        delayed = MessageProxy(target.message())
        middleware.after_process_message(broker, delayed, exception=dramatiq.Retry(delay=1234))
        assert enqueue.call_args.kwargs["delay"] == 1234
        assert not delayed.failed


def test_retry_predicate_receives_original_exception_and_exhaustion_notifies_actor() -> None:
    error = RuntimeError("database unavailable")
    decisions: list[tuple[int, BaseException]] = []

    def predicate(attempt: int, exception: BaseException) -> bool:
        decisions.append((attempt, exception))
        return False

    middleware = SharedRetries()
    broker = StubBroker(middleware=[middleware])

    @dramatiq.actor(broker=broker)
    def exhausted(message: object, metadata: object) -> None:
        pass

    @dramatiq.actor(broker=broker, retry_when=predicate, on_retry_exhausted=exhausted.actor_name, max_retries=3)
    def target() -> None:
        pass

    message = MessageProxy(target.message_with_options(on_retry_exhausted=None))
    with patch.object(broker, "enqueue", wraps=broker.enqueue) as enqueue:
        middleware.after_process_message(broker, message, exception=error)
    assert message.failed
    assert decisions == [(0, error)]
    notification = enqueue.call_args.args[0]
    assert notification.actor_name == exhausted.actor_name
    assert notification.args[1] == {"retries": 0, "max_retries": 3}

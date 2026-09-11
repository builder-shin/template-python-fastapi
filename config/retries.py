"""Shared worker retry schedule, retaining Dramatiq failure and retry hooks."""

from __future__ import annotations

import random
import time
import traceback
from typing import Any

from dramatiq import Retry
from dramatiq.broker import Broker, MessageProxy
from dramatiq.middleware import Retries


class SharedRetries(Retries):
    """Exponential backoff plus integer jitter, identical to the other backends.

    Retry n (1..3) waits 15 * 2**(n-1) + UniformInteger(0, 10*n-1)
    seconds. An explicit Dramatiq Retry(delay=...) still overrides this policy.
    """

    def after_process_message(
        self, broker: Broker, message: MessageProxy, *, result: Any = None, exception: BaseException | None = None
    ) -> None:
        if exception is None:
            return

        actor = broker.get_actor(message.actor_name)
        options = actor.options | message.options
        throws = message.options.get("throws") or actor.options.get("throws")
        if throws and isinstance(exception, throws):
            message.fail()
            return

        completed_retries = message.options.get("retries", 0)
        retry_number = completed_retries + 1
        message.options.update(
            retries=retry_number,
            traceback="".join(traceback.format_exception(exception, limit=30)),
            requeue_timestamp=int(time.time() * 1000),
        )
        maximum = options.get("max_retries", self.max_retries)
        predicate = actor.options.get("retry_when", self.retry_when)
        retry_allowed = (
            predicate(completed_retries, exception)
            if predicate is not None
            else maximum is None or completed_retries < maximum
        )
        if not retry_allowed:
            message.fail()
            self.logger.warning("Retries exhausted for message %s", message.message_id)
            target = message.options.get("on_retry_exhausted") or actor.options.get("on_retry_exhausted")
            if target:
                broker.get_actor(target).send(message.asdict(), {"retries": completed_retries, "max_retries": maximum})
            return

        if isinstance(exception, Retry) and exception.delay is not None:
            delay = exception.delay
        else:
            base = options.get("min_backoff", self.min_backoff)
            cap = min(options.get("max_backoff", self.max_backoff), 604_800_000)
            delay = min(base * 2 ** min(completed_retries, 32) + random.randrange(10 * retry_number) * 1000, cap)
        self.logger.info("Retrying message %s in %d milliseconds", message.message_id, delay)
        broker.enqueue(message.copy(), delay=delay)

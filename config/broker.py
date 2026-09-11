"""Dramatiq Redis broker configuration."""

from __future__ import annotations

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import Retries, default_middleware

from config.retries import SharedRetries
from config.settings import require_env


def configure_broker() -> RedisBroker:
    """Configure and return the process-wide Redis broker."""

    broker = RedisBroker(  # type: ignore[no-untyped-call]
        url=require_env("REDIS_URL"),
        middleware=[SharedRetries() if item is Retries else item() for item in default_middleware],
    )
    dramatiq.set_broker(broker)
    return broker

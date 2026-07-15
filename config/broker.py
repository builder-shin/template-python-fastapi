"""Dramatiq Redis broker configuration."""

from __future__ import annotations

import os

import dramatiq
from dramatiq.brokers.redis import RedisBroker

DEFAULT_REDIS_URL = "redis://localhost:6379/0"


def configure_broker() -> RedisBroker:
    """Configure and return the process-wide Redis broker."""

    broker = RedisBroker(  # type: ignore[no-untyped-call]
        url=os.getenv("REDIS_URL", DEFAULT_REDIS_URL)
    )
    dramatiq.set_broker(broker)
    return broker

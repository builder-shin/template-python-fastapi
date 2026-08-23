"""Dramatiq Redis broker configuration."""

from __future__ import annotations

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from config.settings import require_env


def configure_broker() -> RedisBroker:
    """Configure and return the process-wide Redis broker."""

    broker = RedisBroker(url=require_env("REDIS_URL"))  # type: ignore[no-untyped-call]
    dramatiq.set_broker(broker)
    return broker

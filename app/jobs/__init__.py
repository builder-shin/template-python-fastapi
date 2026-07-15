"""Dramatiq actor exports."""

from config.broker import configure_broker

configure_broker()
from app.jobs.example import process_example  # noqa: E402

__all__ = ["process_example"]

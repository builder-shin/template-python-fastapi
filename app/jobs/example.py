"""Example Dramatiq actor."""

from __future__ import annotations

import logging
from uuid import UUID

import dramatiq

from app.models import Example
from config.database import SessionFactory

logger = logging.getLogger(__name__)


@dramatiq.actor(max_retries=3, min_backoff=15_000)
def process_example(example_id: str) -> None:
    """Read an Example and log the result without changing persisted state."""

    try:
        parsed_id = UUID(example_id)
    except (AttributeError, TypeError, ValueError):
        logger.warning(
            "Skipping Example job with malformed identifier",
            extra={"event": "example.invalid_id", "example_id": str(example_id)},
        )
        return

    with SessionFactory() as session:
        example = session.get(Example, parsed_id)
        if example is None:
            logger.warning(
                "Skipping Example job because the resource does not exist",
                extra={"event": "example.missing", "example_id": example_id},
            )
            return

        logger.info(
            "Processed Example job",
            extra={"event": "example.processed", "example_id": example_id},
        )

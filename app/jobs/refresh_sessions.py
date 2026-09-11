"""Refresh-session retention Dramatiq actor."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import dramatiq
from sqlalchemy import delete, select, text

from app.models import RefreshSession
from config.auth import RefreshSessionRetentionSettings
from config.database import get_session_factory

logger = logging.getLogger(__name__)

# Bounds the wait the ``ON DELETE SET NULL`` cascade can incur on a rotation-chain row
# that ``SKIP LOCKED`` does not cover. Short enough that a stalled batch is retried well
# inside the actor's 15s minimum backoff.
_BATCH_LOCK_TIMEOUT_MS = 2_000
_MAX_BATCHES = 10_000
_MAX_BATCH_SIZE = 2**53 - 1


@dataclass(frozen=True, slots=True)
class PurgeResult:
    """Deleted rows and committed batch attempts, including a final empty batch."""

    deleted: int
    batches: int


@dramatiq.actor(max_retries=3, min_backoff=15_000)
def purge_expired_refresh_sessions(batch_size: int | float = 1_000) -> PurgeResult:
    """Delete refresh sessions whose expiry is older than the retention window.

    Rows are selected by ``expires_at`` only, so a session that can still be
    presented is never removed regardless of the configured window. Deletion
    runs oldest-first in committed batches that skip locked rows, so the job
    never queues behind the ``SELECT ... FOR UPDATE`` rotation takes on a row it
    would have deleted, and surviving rotation chains are left intact.

    ``SKIP LOCKED`` covers only the rows this statement selects. Deleting one of
    them still makes PostgreSQL clear ``replaced_by_id`` on whatever rows point
    at it through the ``ON DELETE SET NULL`` self-reference, and that cascade
    takes its own row locks with no ``SKIP LOCKED`` to fall back on: a chain row
    a concurrent rotation holds would block the batch indefinitely. Every batch
    therefore runs under a short ``lock_timeout``, so contention aborts the batch
    and Dramatiq retries the actor instead of the worker thread stalling. Batches
    committed before that point stay deleted.
    """

    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, (int, float))
        or not 1 <= batch_size <= _MAX_BATCH_SIZE
        or int(batch_size) != batch_size
    ):
        logger.warning(
            "Skipping refresh session purge because the batch size is not a positive safe integer",
            extra={
                "event": "refresh_sessions.invalid_batch_size",
                "batch_size": batch_size,
            },
        )
        return PurgeResult(deleted=0, batches=0)

    batch_size = int(batch_size)

    settings = RefreshSessionRetentionSettings.from_env()
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.retention_seconds)
    expired_ids = (
        select(RefreshSession.id)
        .where(RefreshSession.expires_at < cutoff)
        .order_by(RefreshSession.expires_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
        .cte("candidates")
        .prefix_with("MATERIALIZED")
    )
    # Materialize the locking selection once. A nested-loop rescan of an inline
    # SKIP LOCKED subquery can otherwise select more than batch_size rows.
    statement = (
        delete(RefreshSession).where(RefreshSession.id.in_(select(expired_ids.c.id))).returning(RefreshSession.id)
    )

    deleted = 0
    batches = 0
    session_factory = get_session_factory()
    with session_factory() as session:
        while batches < _MAX_BATCHES:
            # ``SET LOCAL`` binds the timeout to this batch's transaction, so the bound
            # can never outlive the batch on a pooled connection shared with other jobs.
            session.execute(text(f"SET LOCAL lock_timeout = '{_BATCH_LOCK_TIMEOUT_MS}ms'"))
            batch = len(
                session.scalars(
                    statement,
                    execution_options={"synchronize_session": False},
                ).all()
            )
            session.commit()
            deleted += batch
            batches += 1
            if batch < batch_size:
                break
        else:
            logger.warning(
                "Stopped refresh session purge at the batch cap",
                extra={"event": "refresh_sessions.batch_cap", "deleted": deleted, "batches": batches},
            )

    logger.info(
        "Purged expired refresh sessions",
        extra={
            "event": "refresh_sessions.purged",
            "deleted": deleted,
            "batches": batches,
            "cutoff": cutoff.isoformat(),
        },
    )
    return PurgeResult(deleted=deleted, batches=batches)

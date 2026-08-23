"""Refresh-session retention Dramatiq actor."""

from __future__ import annotations

import logging
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


@dramatiq.actor(max_retries=3, min_backoff=15_000)
def purge_expired_refresh_sessions(batch_size: int = 1_000) -> int:
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

    if batch_size <= 0:
        logger.warning(
            "Skipping refresh session purge because the batch size is not positive",
            extra={
                "event": "refresh_sessions.invalid_batch_size",
                "batch_size": batch_size,
            },
        )
        return 0

    settings = RefreshSessionRetentionSettings.from_env()
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.retention_seconds)
    expired_ids = (
        select(RefreshSession.id)
        .where(RefreshSession.expires_at < cutoff)
        .order_by(RefreshSession.expires_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    statement = delete(RefreshSession).where(RefreshSession.id.in_(expired_ids)).returning(RefreshSession.id)

    deleted = 0
    session_factory = get_session_factory()
    with session_factory() as session:
        while True:
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
            if batch < batch_size:
                break

    logger.info(
        "Purged expired refresh sessions",
        extra={
            "event": "refresh_sessions.purged",
            "deleted": deleted,
            "cutoff": cutoff.isoformat(),
        },
    )
    return deleted

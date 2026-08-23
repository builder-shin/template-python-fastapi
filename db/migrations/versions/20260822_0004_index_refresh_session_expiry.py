"""Index refresh-session expiry for the retention purge.

Revision ID: 20260822_0004
Revises: 20260822_0003
Create Date: 2026-08-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260822_0004"
down_revision: str | None = "20260822_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the index that bounds the expired refresh-session purge scan."""

    op.create_index(
        op.f("ix_refresh_sessions_expires_at"),
        "refresh_sessions",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the refresh-session expiry index."""

    op.drop_index(op.f("ix_refresh_sessions_expires_at"), table_name="refresh_sessions")

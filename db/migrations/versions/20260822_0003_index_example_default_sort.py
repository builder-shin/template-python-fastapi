"""Index the default example list ordering.

Revision ID: 20260822_0003
Revises: 20260715_0002
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0003"
down_revision: str | None = "20260715_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the composite index covering ``created_at DESC, id`` list ordering."""

    op.create_index(
        op.f("ix_examples_created_at_id"),
        "examples",
        [sa.text("created_at DESC"), sa.text("id")],
        unique=False,
    )


def downgrade() -> None:
    """Drop the composite example ordering index."""

    op.drop_index(op.f("ix_examples_created_at_id"), table_name="examples")

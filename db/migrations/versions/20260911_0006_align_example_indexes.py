"""Align the shared example ordering access paths.

Revision ID: 20260911_0006
Revises: 20260911_0005
Create Date: 2026-09-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260911_0006"
down_revision: str | None = "20260911_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Retain default-order/FK indexes and add the shared title access path."""
    op.create_index("ix_examples_title_id", "examples", ["title", "id"])


def downgrade() -> None:
    """Restore the prior set of indexes."""
    op.drop_index("ix_examples_title_id", table_name="examples")

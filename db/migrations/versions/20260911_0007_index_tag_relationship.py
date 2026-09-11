"""Align reverse tag lookups and tag deletion cascades.

Revision ID: 20260911_0007
Revises: 20260911_0006
Create Date: 2026-09-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260911_0007"
down_revision: str | None = "20260911_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Cover the FK not covered by the association's composite primary key."""
    op.create_index("ix_example_tags_tag_id", "example_tags", ["tag_id"])


def downgrade() -> None:
    """Restore the original association indexes."""
    op.drop_index("ix_example_tags_tag_id", table_name="example_tags")

"""Example resource model and tag association."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Column, Enum, ForeignKey, Integer, String, Table, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.example_category import ExampleCategory
from app.models.example_tag import ExampleTag


class ExampleStatus(StrEnum):
    """Lifecycle status stored by its public JSON value."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


example_tags = Table(
    "example_tags",
    Base.metadata,
    Column(
        "example_id",
        PG_UUID(as_uuid=True),
        ForeignKey("examples.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        PG_UUID(as_uuid=True),
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Example(TimestampMixin, Base):
    """Example resource used to demonstrate the API conventions."""

    __tablename__ = "examples"
    __table_args__ = (CheckConstraint("score >= 0 AND score <= 100", name="score_range"),)

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    status: Mapped[ExampleStatus] = mapped_column(
        Enum(
            ExampleStatus,
            name="example_status",
            values_callable=lambda statuses: [status.value for status in statuses],
            validate_strings=True,
        ),
        nullable=False,
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    category_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        index=True,
    )
    category: Mapped[ExampleCategory | None] = relationship(
        back_populates="examples",
        cascade="save-update, merge",
    )
    tags: Mapped[list[ExampleTag]] = relationship(
        secondary=example_tags,
        back_populates="examples",
        cascade="save-update, merge",
        passive_deletes=True,
    )

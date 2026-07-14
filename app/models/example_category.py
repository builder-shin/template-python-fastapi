"""Example category model."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.example import Example


class ExampleCategory(TimestampMixin, Base):
    """Category shared by example resources."""

    __tablename__ = "categories"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    examples: Mapped[list[Example]] = relationship(
        back_populates="category",
        cascade="save-update, merge",
        passive_deletes=True,
    )

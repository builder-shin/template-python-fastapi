"""Migration-owned email rollback history, never exposed as an API resource."""

from uuid import UUID

from sqlalchemy import PrimaryKeyConstraint, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EmailIdentityBackup(Base):
    __tablename__ = "email_identity_backups"
    __table_args__ = (PrimaryKeyConstraint("user_id", name="PK_email_identity_backups"),)
    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    original_email: Mapped[str] = mapped_column(String(254), nullable=False)
    canonical_email: Mapped[str] = mapped_column(String(254), nullable=False)

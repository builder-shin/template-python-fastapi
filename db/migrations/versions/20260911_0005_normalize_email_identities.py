"""Normalize legacy email identity and preserve exact rollback representations."""

from collections.abc import Sequence

from alembic import op
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.schemas.auth import AuthEmail, normalize_email

revision: str = "20260911_0005"
down_revision: str | None = "20260822_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    normalize_identities(op.get_bind())


def downgrade() -> None:
    restore_identities(op.get_bind())


def normalize_identities(connection: Connection) -> None:
    connection.execute(text("LOCK TABLE users IN ACCESS EXCLUSIVE MODE"))
    users = connection.execute(text("SELECT id,email FROM users ORDER BY id")).mappings().all()
    seen: set[str] = set()
    changed = []
    for user in users:
        try:
            canonical = normalize_email(str(TypeAdapter(AuthEmail).validate_python(user["email"])))
        except ValueError as error:
            raise ValueError(
                "Email identity migration requires explicit correction of an address rejected by login validation"
            ) from error
        if canonical in seen:
            raise ValueError("Email normalization collision requires explicit account correction")
        seen.add(canonical)
        if canonical != user["email"]:
            changed.append({"id": user["id"], "original": user["email"], "canonical": canonical})
    connection.execute(
        text(
            'CREATE TABLE email_identity_backups (user_id uuid NOT NULL, original_email varchar(254) NOT NULL, canonical_email varchar(254) NOT NULL, CONSTRAINT "PK_email_identity_backups" PRIMARY KEY (user_id))'
        )
    )
    for changed_user in changed:
        connection.execute(text("INSERT INTO email_identity_backups VALUES (:id,:original,:canonical)"), changed_user)
        connection.execute(text("UPDATE users SET email=:canonical WHERE id=:id"), changed_user)


def restore_identities(connection: Connection) -> None:
    connection.execute(text("LOCK TABLE users, email_identity_backups IN ACCESS EXCLUSIVE MODE"))
    users = (
        connection.execute(
            text(
                "SELECT u.id,u.email,b.original_email,b.canonical_email FROM users u LEFT JOIN email_identity_backups b ON b.user_id=u.id ORDER BY u.id"
            )
        )
        .mappings()
        .all()
    )
    seen: set[str] = set()
    for user in users:
        target = user["original_email"] if user["email"] == user["canonical_email"] else user["email"]
        if target in seen:
            raise ValueError("Email rollback collision requires explicit account correction")
        seen.add(target)
    for user in users:
        if user["email"] == user["canonical_email"]:
            connection.execute(text("UPDATE users SET email=:original_email WHERE id=:id"), dict(user))
    connection.execute(text("DROP TABLE email_identity_backups"))

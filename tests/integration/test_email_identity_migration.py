"""Real PostgreSQL identity migration and faithful rollback regression."""

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, text

SPEC = importlib.util.spec_from_file_location(
    "email_identity_migration",
    Path(__file__).parents[2] / "db/migrations/versions/20260911_0005_normalize_email_identities.py",
)
assert SPEC and SPEC.loader
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


@pytest.fixture
def connection(db_engine: Engine) -> Iterator[Connection]:
    with db_engine.connect() as connection, connection.begin():
        connection.execute(text("CREATE SCHEMA email_identity_probe"))
        connection.execute(text("SET LOCAL search_path TO email_identity_probe"))
        connection.execute(text("CREATE TABLE users (id uuid PRIMARY KEY,email varchar(254) UNIQUE NOT NULL)"))
        yield connection
        connection.rollback()


def insert(connection: Connection, email: str) -> UUID:
    identifier = uuid4()
    connection.execute(text("INSERT INTO users VALUES (:id,:email)"), {"id": identifier, "email": email})
    return identifier


def email_of(connection: Connection, identifier: UUID) -> str | None:
    value = connection.execute(text("SELECT email FROM users WHERE id=:id"), {"id": identifier}).scalar()
    assert value is None or isinstance(value, str)
    return value


def test_identity_roundtrip(connection: Connection) -> None:
    original = "Cafe\u0301@XN--BCHER-KVA.EXAMPLE.COM"
    identifier = insert(connection, original)
    MIGRATION.normalize_identities(connection)
    assert email_of(connection, identifier) == "café@bücher.example.com"
    MIGRATION.restore_identities(connection)
    assert email_of(connection, identifier) == original


@pytest.mark.parametrize(
    "first,second",
    [
        ("A@example.com", "a@example.com"),
        ("a@xn--bcher-kva.example.com", "a@bücher.example.com"),
        ("Straße@example.com", "strasse@example.com"),
        ("cafe\u0301@example.com", "café@example.com"),
    ],
)
def test_collision_preflight(connection: Connection, first: str, second: str) -> None:
    identifier = insert(connection, first)
    insert(connection, second)
    with pytest.raises(ValueError, match="collision"):
        MIGRATION.normalize_identities(connection)
    assert email_of(connection, identifier) == first


def test_invalid_legacy_preflight(connection: Connection) -> None:
    identifier = insert(connection, "legacy@example.test")
    with pytest.raises(ValueError, match="correction"):
        MIGRATION.normalize_identities(connection)
    assert email_of(connection, identifier) == "legacy@example.test"


def test_later_edits_deletions_and_down_collision(connection: Connection) -> None:
    edited = insert(connection, "Edited@example.com")
    deleted = insert(connection, "Deleted@example.com")
    original = insert(connection, "Original@example.com")
    MIGRATION.normalize_identities(connection)
    connection.execute(text("UPDATE users SET email=:email WHERE id=:id"), {"email": "new@example.com", "id": edited})
    connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": deleted})
    collision = insert(connection, "Original@example.com")
    with pytest.raises(ValueError, match="collision"):
        MIGRATION.restore_identities(connection)
    assert email_of(connection, original) == "original@example.com"
    connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": collision})
    MIGRATION.restore_identities(connection)
    assert email_of(connection, edited) == "new@example.com"
    assert email_of(connection, deleted) is None
    assert email_of(connection, original) == "Original@example.com"

"""Fresh PostgreSQL database migration integration tests."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url

API_ROOT = Path(__file__).resolve().parents[2]


def _index_names(engine: Engine, table: str) -> set[str]:
    return {name for index in inspect(engine).get_indexes(table) if (name := index["name"]) is not None}


def _example_index_names(engine: Engine) -> set[str]:
    return _index_names(engine, "examples")


def _refresh_session_index_names(engine: Engine) -> set[str]:
    return _index_names(engine, "refresh_sessions")


def test_upgrade_head_builds_required_tables_in_an_empty_database(
    monkeypatch: pytest.MonkeyPatch,
    test_database_url: str,
) -> None:
    source_url = make_url(test_database_url)
    database_name = f"fastapi_template_migration_{uuid4().hex}_test"
    admin_url = source_url.set(database="postgres")
    migrated_url = source_url.set(database=database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    quoted_name = admin_engine.dialect.identifier_preparer.quote(database_name)

    with admin_engine.connect() as connection:
        connection.execute(text(f"CREATE DATABASE {quoted_name}"))

    migrated_engine = create_engine(migrated_url)
    try:
        assert inspect(migrated_engine).get_table_names() == []
        config = Config(str(API_ROOT / "alembic.ini"))
        config.set_main_option(
            "sqlalchemy.url",
            migrated_url.render_as_string(hide_password=False),
        )
        monkeypatch.setenv(
            "TEST_DATABASE_URL",
            migrated_url.render_as_string(hide_password=False),
        )

        command.upgrade(config, "head")

        assert set(inspect(migrated_engine).get_table_names()) == {
            "alembic_version",
            "categories",
            "tags",
            "examples",
            "example_tags",
            "users",
            "refresh_sessions",
        }
        assert "ix_examples_created_at_id" in _example_index_names(migrated_engine)
        assert "ix_refresh_sessions_expires_at" in _refresh_session_index_names(migrated_engine)

        command.downgrade(config, "20260822_0003")

        assert "ix_refresh_sessions_expires_at" not in _refresh_session_index_names(migrated_engine)

        command.downgrade(config, "20260715_0002")

        assert "ix_examples_created_at_id" not in _example_index_names(migrated_engine)

        command.downgrade(config, "20260714_0001")

        assert set(inspect(migrated_engine).get_table_names()) == {
            "alembic_version",
            "categories",
            "tags",
            "examples",
            "example_tags",
        }

        command.upgrade(config, "head")

        assert {"users", "refresh_sessions"}.issubset(inspect(migrated_engine).get_table_names())
        assert "ix_refresh_sessions_expires_at" in _refresh_session_index_names(migrated_engine)
    finally:
        migrated_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f"DROP DATABASE {quoted_name} WITH (FORCE)"))
        admin_engine.dispose()

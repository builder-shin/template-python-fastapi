"""Exact PostgreSQL index rollback, including data and column defaults."""

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, Engine, text


def snapshot(connection: Connection, sql: str) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in connection.execute(text(sql))]


@pytest.mark.parametrize(
    "filename,definition",
    [
        ("20260911_0006_align_example_indexes.py", "btree (title, id)"),
        ("20260911_0007_index_tag_relationship.py", "btree (tag_id)"),
    ],
)
def test_exact_index_roundtrip(db_engine: Engine, filename: str, definition: str) -> None:
    spec = importlib.util.spec_from_file_location(
        "index_migration", Path(__file__).parents[2] / "db/migrations/versions" / filename
    )
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with db_engine.connect() as connection, connection.begin():
        connection.execute(text("CREATE SCHEMA index_roundtrip_probe"))
        connection.execute(text("SET LOCAL search_path TO index_roundtrip_probe"))
        connection.execute(
            text("""
            CREATE TABLE examples (id uuid PRIMARY KEY, title varchar(200) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(), category_id uuid,
            status varchar(20) NOT NULL, score integer NOT NULL)
        """)
        )
        connection.execute(text("CREATE INDEX ix_examples_created_at_id ON examples(created_at DESC,id)"))
        connection.execute(text("CREATE INDEX ix_examples_category_id ON examples(category_id)"))
        connection.execute(
            text("CREATE TABLE example_tags(example_id uuid,tag_id uuid,PRIMARY KEY(example_id,tag_id))")
        )
        connection.execute(
            text("""
            INSERT INTO examples VALUES ('00000000-0000-0000-0000-000000000001','kept',
            '2026-09-11 01:02:03.123456+00',NULL,'active',42)
        """)
        )
        connection.execute(
            text("""
            INSERT INTO example_tags VALUES ('00000000-0000-0000-0000-000000000001',
            '00000000-0000-0000-0000-000000000002')
        """)
        )
        indexes_sql = "SELECT indexdef FROM pg_indexes WHERE schemaname=current_schema() ORDER BY indexname"
        columns_sql = """SELECT table_name,column_name,column_default,is_nullable,data_type
            FROM information_schema.columns WHERE table_schema=current_schema() ORDER BY table_name,ordinal_position"""
        rows_sql = (
            "SELECT row_to_json(t)::text FROM examples t UNION ALL SELECT row_to_json(t)::text FROM example_tags t"
        )
        before = snapshot(connection, indexes_sql)
        columns = snapshot(connection, columns_sql)
        rows = snapshot(connection, rows_sql)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert any(str(row[0]).endswith(definition) for row in snapshot(connection, indexes_sql))
            assert snapshot(connection, indexes_sql) != before
            assert snapshot(connection, columns_sql) == columns
            assert snapshot(connection, rows_sql) == rows
            migration.downgrade()
        assert snapshot(connection, indexes_sql) == before
        assert snapshot(connection, columns_sql) == columns
        assert snapshot(connection, rows_sql) == rows
        connection.rollback()

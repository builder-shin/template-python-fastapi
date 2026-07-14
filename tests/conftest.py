"""Database fixtures that activate only when explicitly requested."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

API_ROOT = Path(__file__).resolve().parents[1]


def _require_test_database_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.fail("TEST_DATABASE_URL is required for database tests")

    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        pytest.fail("database tests require a database name ending in '_test'")
    return database_url


def _alembic_config(database_url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _truncate_tables(engine: Engine) -> None:
    table_names = ", ".join(f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables))
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def test_database_url() -> str:
    return _require_test_database_url()


@pytest.fixture(scope="session")
def migrated_database(test_database_url: str) -> Iterator[str]:
    command.upgrade(_alembic_config(test_database_url), "head")
    yield test_database_url


@pytest.fixture(scope="session")
def db_engine(migrated_database: str) -> Iterator[Engine]:
    engine = create_engine(migrated_database, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def committed_session(db_engine: Engine) -> Iterator[Session]:
    _truncate_tables(db_engine)
    session = Session(bind=db_engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        _truncate_tables(db_engine)


@pytest.fixture
def concurrent_session_factory(db_engine: Engine) -> Iterator[Callable[[], Session]]:
    _truncate_tables(db_engine)
    sessions: list[Session] = []
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)

    def create_session() -> Session:
        session = factory()
        sessions.append(session)
        return session

    try:
        yield create_session
    finally:
        for session in sessions:
            session.close()
        _truncate_tables(db_engine)

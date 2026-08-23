"""Database configuration tests."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from inspect import isgeneratorfunction
from typing import cast
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from config import database
from config.database import DatabaseSettings, build_engine
from config.main import create_app

TEST_DATABASE_URL = (
    "postgresql+psycopg://fastapi:fastapi@localhost:55432/fastapi_template_test"  # pragma: allowlist secret
)


def _application_with_session_factory(session_factory: object) -> FastAPI:
    app = FastAPI()
    app.state.session_factory = session_factory
    return app


def _request(app: FastAPI) -> Request:
    return Request({"type": "http", "app": app})


def test_database_settings_use_balanced_pool_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.delenv("DB_POOL_SIZE", raising=False)
    monkeypatch.delenv("DB_MAX_OVERFLOW", raising=False)
    monkeypatch.delenv("DB_POOL_TIMEOUT", raising=False)

    settings = DatabaseSettings.from_env()

    assert settings.url == TEST_DATABASE_URL
    assert settings.pool_size == 5
    assert settings.max_overflow == 10
    assert settings.pool_timeout == 30


def test_database_settings_read_pool_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("DB_POOL_SIZE", "7")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "8")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "9")

    settings = DatabaseSettings.from_env()

    assert settings.pool_size == 7
    assert settings.max_overflow == 8
    assert settings.pool_timeout == 9


@pytest.mark.parametrize("value", [None, "", "   "])
def test_database_settings_require_an_explicit_url(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", value)

    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        DatabaseSettings.from_env()


@pytest.mark.parametrize("variable", ["DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT"])
def test_database_settings_name_the_variable_for_non_integer_pool_values(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
) -> None:
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv(variable, "abc")

    with pytest.raises(ValueError, match=f"{variable} must be an integer"):
        DatabaseSettings.from_env()


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("DB_POOL_SIZE", "0", "pool_size must be at least 1"),
        ("DB_MAX_OVERFLOW", "-1", "max_overflow must be at least 0"),
        ("DB_POOL_TIMEOUT", "0", "pool_timeout must be greater than 0"),
    ],
)
def test_database_settings_reject_invalid_pool_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError, match=message):
        DatabaseSettings.from_env()


def test_build_engine_uses_configured_pool_settings() -> None:
    settings = DatabaseSettings(
        url=TEST_DATABASE_URL,
        pool_size=6,
        max_overflow=12,
        pool_timeout=45,
    )

    configured_engine = build_engine(settings)

    pool = cast(QueuePool, configured_engine.pool)
    assert pool.size() == 6
    assert pool.timeout() == 45
    assert pool._max_overflow == 12
    assert pool._pre_ping is True
    configured_engine.dispose()


def test_build_session_factory_binds_a_new_engine() -> None:
    settings = DatabaseSettings(url=TEST_DATABASE_URL, pool_size=4)

    factory = database.build_session_factory(settings)

    engine = factory.kw["bind"]
    assert factory.kw["expire_on_commit"] is False
    assert engine.pool.size() == 4
    engine.dispose()


def test_importing_the_module_does_not_build_an_engine() -> None:
    script = """
import sqlalchemy


def forbidden_create_engine(*args, **kwargs):
    raise AssertionError("importing config.database created an engine")


sqlalchemy.create_engine = forbidden_create_engine

import config.database as database

assert not hasattr(database, "engine")
assert not hasattr(database, "SessionFactory")
assert database._session_factory is None
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        env=os.environ.copy(),
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_get_session_factory_builds_once_and_reuses_the_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database, "_session_factory", None)
    factory = MagicMock(name="session_factory")
    build_session_factory = MagicMock(return_value=factory)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setattr(database, "build_session_factory", build_session_factory)

    first = database.get_session_factory()
    second = database.get_session_factory()

    assert first is factory
    assert second is factory
    build_session_factory.assert_called_once_with(DatabaseSettings.from_env())


def test_get_session_factory_builds_one_factory_under_concurrent_cold_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cold Dramatiq worker must not hand every thread its own engine.

    Building the first factory is slow (the one-time psycopg DBAPI import), so a
    bare check-then-set lets every thread that arrives during that window build
    its own engine and its own pool.
    """

    monkeypatch.setattr(database, "_session_factory", None)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    factories: list[object] = []

    def slow_build_session_factory(settings: DatabaseSettings) -> object:
        time.sleep(0.05)
        factory = MagicMock(name=f"session_factory_{len(factories)}")
        factories.append(factory)
        return factory

    monkeypatch.setattr(database, "build_session_factory", slow_build_session_factory)
    thread_count = 8
    barrier = threading.Barrier(thread_count)
    handed_out: list[object] = []
    handed_out_lock = threading.Lock()

    def call_get_session_factory() -> None:
        barrier.wait()
        factory = database.get_session_factory()
        with handed_out_lock:
            handed_out.append(factory)

    threads = [threading.Thread(target=call_get_session_factory) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(factories) == 1
    assert len(handed_out) == thread_count
    assert {id(factory) for factory in handed_out} == {id(factories[0])}


def test_get_session_closes_session() -> None:
    fake_session = MagicMock(spec=Session)
    fake_context = MagicMock()
    fake_context.__enter__.return_value = fake_session
    app = _application_with_session_factory(lambda: fake_context)

    # get_session is declared as Iterator[Session]; close() belongs to the generator it really is.
    generator = cast(Generator[Session, None, None], database.get_session(_request(app)))

    assert next(generator) is fake_session
    generator.close()
    fake_context.__exit__.assert_called_once()


def test_get_request_session_binds_the_endpoint_session_to_request_state() -> None:
    fake_session = MagicMock(spec=Session)
    request = _request(_application_with_session_factory(MagicMock()))

    resolved = database.get_request_session(request, fake_session)

    assert resolved is fake_session
    assert request.state.session is fake_session


def test_get_request_session_resolves_its_session_through_get_session() -> None:
    """Wrapping ``get_session`` keeps ``dependency_overrides[get_session]`` effective."""

    dependant = database.get_request_session.__defaults__
    assert dependant is not None
    assert dependant[0].dependency is database.get_session


def test_get_auth_session_factory_does_not_bind_request_state_session() -> None:
    """The auth session must never shadow the write session on protected routes."""

    request = _request(_application_with_session_factory(MagicMock()))

    database.get_auth_session_factory(request)

    assert not hasattr(request.state, "session")


def test_get_auth_session_factory_returns_the_application_session_factory() -> None:
    session_factory = MagicMock()
    app = _application_with_session_factory(session_factory)

    resolved = database.get_auth_session_factory(_request(app))

    assert resolved is session_factory
    assert not isgeneratorfunction(database.get_auth_session_factory)
    session_factory.assert_not_called()


def test_get_auth_session_factory_does_not_open_a_session_on_resolution() -> None:
    """The auth dependency hands back a factory, not an already open session."""

    fake_session = MagicMock(spec=Session)
    fake_context = MagicMock()
    fake_context.__enter__.return_value = fake_session
    session_factory = MagicMock(return_value=fake_context)
    app = _application_with_session_factory(session_factory)

    resolved = database.get_auth_session_factory(_request(app))
    session_factory.assert_not_called()

    with resolved() as session:
        assert session is fake_session
    fake_context.__exit__.assert_called_once()


def test_create_app_fails_closed_without_a_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        create_app()


def test_create_app_stores_the_engine_and_session_factory_on_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("DB_POOL_SIZE", "3")

    app = create_app()

    assert app.state.auth_settings is not None
    assert app.state.database_settings == DatabaseSettings.from_env()
    assert str(app.state.engine.url) == str(build_engine(DatabaseSettings.from_env()).url)
    assert app.state.engine.pool.size() == 3
    assert app.state.session_factory.kw["bind"] is app.state.engine
    assert app.state.session_factory.kw["expire_on_commit"] is False
    app.state.engine.dispose()


def test_application_lifespan_disposes_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    app = create_app()
    dispose = MagicMock()
    monkeypatch.setattr(app.state.engine, "dispose", dispose)

    with TestClient(app):
        dispose.assert_not_called()

    dispose.assert_called_once_with()

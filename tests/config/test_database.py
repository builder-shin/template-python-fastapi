"""Database configuration tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from config import database
from config.database import DatabaseSettings, build_engine


def test_database_settings_use_balanced_pool_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://fastapi:fastapi@localhost:55432/fastapi_template_test",  # pragma: allowlist secret
    )
    monkeypatch.delenv("DB_POOL_SIZE", raising=False)
    monkeypatch.delenv("DB_MAX_OVERFLOW", raising=False)
    monkeypatch.delenv("DB_POOL_TIMEOUT", raising=False)

    settings = DatabaseSettings.from_env()

    assert settings.pool_size == 5
    assert settings.max_overflow == 10
    assert settings.pool_timeout == 30


def test_database_settings_read_pool_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://fastapi:fastapi@localhost:55432/fastapi_template_test",  # pragma: allowlist secret
    )
    monkeypatch.setenv("DB_POOL_SIZE", "7")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "8")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "9")

    settings = DatabaseSettings.from_env()

    assert settings.pool_size == 7
    assert settings.max_overflow == 8
    assert settings.pool_timeout == 9


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
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError, match=message):
        DatabaseSettings.from_env()


def test_build_engine_uses_configured_pool_settings() -> None:
    settings = DatabaseSettings(
        url="postgresql+psycopg://fastapi:fastapi@localhost:55432/fastapi_template_test",  # pragma: allowlist secret
        pool_size=6,
        max_overflow=12,
        pool_timeout=45,
    )

    configured_engine = build_engine(settings)

    assert configured_engine.pool.size() == 6
    assert configured_engine.pool.timeout() == 45
    assert configured_engine.pool._max_overflow == 12
    assert configured_engine.pool._pre_ping is True
    configured_engine.dispose()


def test_get_session_closes_session(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = MagicMock(spec=Session)
    fake_context = MagicMock()
    fake_context.__enter__.return_value = fake_session
    monkeypatch.setattr(database, "SessionFactory", lambda: fake_context)

    generator = database.get_session()

    assert next(generator) is fake_session
    generator.close()
    fake_context.__exit__.assert_called_once()

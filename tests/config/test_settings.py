"""Shared environment loading helper tests."""

from __future__ import annotations

import pytest

from config.settings import read_int, require_env


def test_require_env_returns_the_configured_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXAMPLE_SETTING", "configured")

    assert require_env("EXAMPLE_SETTING") == "configured"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_require_env_fails_closed_and_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", value)

    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        require_env("DATABASE_URL")


def test_read_int_uses_the_default_when_the_variable_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DB_POOL_SIZE", raising=False)

    assert read_int("DB_POOL_SIZE", 5) == 5


def test_read_int_reads_the_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_POOL_SIZE", "11")

    assert read_int("DB_POOL_SIZE", 5) == 11


def test_read_int_names_the_variable_for_non_integer_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_POOL_SIZE", "abc")

    with pytest.raises(ValueError, match="DB_POOL_SIZE must be an integer"):
        read_int("DB_POOL_SIZE", 5)

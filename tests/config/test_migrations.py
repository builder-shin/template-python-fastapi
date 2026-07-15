"""Alembic database URL resolution tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

API_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic(environment: dict[str, str], *arguments: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=API_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    return output


def test_alembic_uses_database_url_without_test_override(test_database_url: str) -> None:
    environment = os.environ.copy()
    environment.pop("TEST_DATABASE_URL", None)
    environment["DATABASE_URL"] = test_database_url

    _run_alembic(environment, "downgrade", "base")
    _run_alembic(environment, "upgrade", "head")
    assert "20260715_0002 (head)" in _run_alembic(environment, "current")
    assert "No new upgrade operations detected" in _run_alembic(environment, "check")


def test_alembic_prefers_test_database_url(test_database_url: str) -> None:
    environment = os.environ.copy()
    environment["TEST_DATABASE_URL"] = test_database_url
    environment["DATABASE_URL"] = "postgresql+psycopg://127.0.0.1:1/ignored"

    assert "20260715_0002 (head)" in _run_alembic(environment, "current")


def test_alembic_falls_back_to_explicit_config_url(
    monkeypatch: pytest.MonkeyPatch,
    test_database_url: str,
) -> None:
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", test_database_url)

    command.current(config)

    engine = create_engine(test_database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260715_0002"
    finally:
        engine.dispose()


def test_alembic_fails_closed_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config = Config(str(API_ROOT / "alembic.ini"))

    with pytest.raises(RuntimeError, match="database URL is required"):
        command.current(config)

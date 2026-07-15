"""Health endpoint integration tests."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.jsonapi import JSONAPI_MEDIA_TYPE
from config.database import get_session
from config.main import create_app


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_liveness_needs_no_accept_header_or_database(
    app: FastAPI,
    client: TestClient,
) -> None:
    def fail_if_database_is_requested() -> Iterator[Session]:
        raise AssertionError("liveness must not resolve a database session")
        yield  # pragma: no cover - makes this a dependency generator

    app.dependency_overrides[get_session] = fail_if_database_is_requested

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json() == {
        "data": None,
        "meta": {"status": "ok"},
        "jsonapi": {"version": "1.1"},
    }


def test_readiness_executes_select_one_without_accept_header(
    app: FastAPI,
    client: TestClient,
) -> None:
    session = MagicMock(spec=Session)

    def override_session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_session

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json() == {
        "data": None,
        "meta": {"status": "ok"},
        "jsonapi": {"version": "1.1"},
    }
    session.execute.assert_called_once()
    statement = session.execute.call_args.args[0]
    assert str(statement) == "SELECT 1"


def test_readiness_returns_safe_jsonapi_error_when_database_is_unavailable(
    app: FastAPI,
    client: TestClient,
) -> None:
    session = MagicMock(spec=Session)
    session.execute.side_effect = OperationalError(
        "SELECT 1",
        {},
        Exception("database-private-detail"),
    )

    def override_session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_session

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert "data" not in response.json()
    assert response.json()["errors"][0]["code"] == "INTERNAL_SERVER_ERROR"
    assert response.json()["errors"][0]["status"] == "503"
    assert "database-private-detail" not in response.text

"""Production Example JSON:API controller integration tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from starlette.routing import Route

from app.jsonapi import JSONAPI_MEDIA_TYPE
from app.models import Example, ExampleStatus
from app.serializers import ExampleSerializer
from config.database import get_session
from config.main import create_app


@pytest.fixture
def app(db_engine: Engine) -> FastAPI:
    application = create_app()

    def override_session() -> Iterator[Session]:
        with Session(bind=db_engine, expire_on_commit=False) as session:
            yield session

    application.dependency_overrides[get_session] = override_session
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _persist_example(session: Session) -> Example:
    example = Example(
        title="통합 예시",
        description=None,
        status=ExampleStatus.ACTIVE,
        score=90,
    )
    ExampleSerializer.initialize_relationship_defaults(example)
    session.add(example)
    session.commit()
    return example


def test_openapi_exposes_only_declared_example_resource_operations(app: FastAPI) -> None:
    paths = {path: item for path, item in app.openapi()["paths"].items() if path.startswith("/api/v1/examples")}

    assert {path: set(item) for path, item in paths.items()} == {
        "/api/v1/examples": {"get", "post"},
        "/api/v1/examples/{resource_id}": {"get", "patch", "put", "delete"},
        "/api/v1/examples/{resource_id}/relationships/category": {"get", "patch"},
        "/api/v1/examples/{resource_id}/category": {"get"},
        "/api/v1/examples/{resource_id}/relationships/tags": {
            "get",
            "post",
            "patch",
            "delete",
        },
        "/api/v1/examples/{resource_id}/tags": {"get"},
    }


def test_index_returns_jsonapi_collection(
    client: TestClient,
    committed_session: Session,
) -> None:
    example = _persist_example(committed_session)

    response = client.get(
        "/api/v1/examples",
        headers={"Accept": JSONAPI_MEDIA_TYPE},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert [resource["id"] for resource in response.json()["data"]] == [str(example.id)]
    assert response.json()["data"][0]["type"] == "examples"
    assert response.json()["data"][0]["attributes"]["title"] == "통합 예시"


def test_application_exposes_only_explicitly_composed_routes(app: FastAPI) -> None:
    assert {route.path for route in app.routes if isinstance(route, Route)} == {
        "/api/schema",
        "/api-docs",
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/examples",
        "/api/v1/examples/{resource_id}",
        "/api/v1/examples/{resource_id}/relationships/category",
        "/api/v1/examples/{resource_id}/category",
        "/api/v1/examples/{resource_id}/relationships/tags",
        "/api/v1/examples/{resource_id}/tags",
    }


def test_legacy_example_route_is_removed(client: TestClient) -> None:
    response = client.get("/api/v1/example")

    assert response.status_code == 404
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json()["errors"][0]["code"] == "RESOURCE_NOT_FOUND"

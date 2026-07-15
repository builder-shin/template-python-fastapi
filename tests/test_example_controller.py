"""Production Example JSON:API controller integration tests."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from starlette.routing import Route

from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.jsonapi import JSONAPI_MEDIA_TYPE
from app.models import Example, ExampleCategory, ExampleStatus, ExampleTag, User
from app.serializers import ExampleSerializer
from config.auth import AuthSettings
from config.database import get_auth_session, get_session
from config.main import create_app


@pytest.fixture
def app(db_engine: Engine) -> FastAPI:
    application = create_app()

    def override_session() -> Iterator[Session]:
        with Session(bind=db_engine, expire_on_commit=False) as session:
            yield session

    application.dependency_overrides[get_session] = override_session
    application.dependency_overrides[get_auth_session] = override_session
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


def _persist_user(session: Session, *, is_active: bool = True) -> User:
    user = User(
        email=f"example-{uuid4()}@example.com",
        password_hash=hash_password("example-write-password"),  # pragma: allowlist secret
        is_active=is_active,
    )
    session.add(user)
    session.commit()
    return user


def _access_token(app: FastAPI, user: User) -> str:
    settings = app.state.auth_settings
    assert isinstance(settings, AuthSettings)
    return create_token(user.id, token_type="access", settings=settings)


def _create_document(*, title: str = "보호된 생성") -> dict[str, object]:
    return {
        "data": {
            "type": "examples",
            "attributes": {
                "title": title,
                "description": None,
                "status": "active",
                "score": 80,
            },
        }
    }


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
        "/api/v1/auth/logout",
        "/api/v1/auth/refresh",
        "/api/v1/auth/register",
        "/api/v1/users/me",
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


def test_all_example_read_routes_remain_public(
    client: TestClient,
    committed_session: Session,
) -> None:
    category = ExampleCategory(name="공개 카테고리")
    tag = ExampleTag(name="공개 태그")
    example = Example(
        title="공개 조회",
        description=None,
        status=ExampleStatus.ACTIVE,
        score=90,
        category=category,
        tags=[tag],
    )
    committed_session.add(example)
    committed_session.commit()

    paths = (
        "/api/v1/examples",
        f"/api/v1/examples/{example.id}",
        f"/api/v1/examples/{example.id}/relationships/category",
        f"/api/v1/examples/{example.id}/category",
        f"/api/v1/examples/{example.id}/relationships/tags",
        f"/api/v1/examples/{example.id}/tags",
    )

    for path in paths:
        response = client.get(path, headers={"Accept": JSONAPI_MEDIA_TYPE})
        assert response.status_code == 200, (path, response.text)


def test_all_example_write_routes_require_bearer_authentication(
    client: TestClient,
    committed_session: Session,
) -> None:
    category = ExampleCategory(name="보호 카테고리")
    tag = ExampleTag(name="보호 태그")
    example = _persist_example(committed_session)
    committed_session.add_all([category, tag])
    committed_session.commit()
    resource_id = str(example.id)
    created_id = str(uuid4())
    headers = {"Accept": JSONAPI_MEDIA_TYPE, "Content-Type": JSONAPI_MEDIA_TYPE}
    requests = (
        ("POST", "/api/v1/examples", _create_document()),
        (
            "PATCH",
            f"/api/v1/examples/{resource_id}",
            {"data": {"type": "examples", "id": resource_id, "attributes": {"title": "변경"}}},
        ),
        (
            "PUT",
            f"/api/v1/examples/{created_id}",
            {
                "data": {
                    "type": "examples",
                    "id": created_id,
                    "attributes": _create_document()["data"]["attributes"],  # type: ignore[index]
                }
            },
        ),
        ("DELETE", f"/api/v1/examples/{resource_id}", None),
        (
            "PATCH",
            f"/api/v1/examples/{resource_id}/relationships/category",
            {"data": {"type": "exampleCategories", "id": str(category.id)}},
        ),
        (
            "POST",
            f"/api/v1/examples/{resource_id}/relationships/tags",
            {"data": [{"type": "exampleTags", "id": str(tag.id)}]},
        ),
        (
            "PATCH",
            f"/api/v1/examples/{resource_id}/relationships/tags",
            {"data": [{"type": "exampleTags", "id": str(tag.id)}]},
        ),
        (
            "DELETE",
            f"/api/v1/examples/{resource_id}/relationships/tags",
            {"data": [{"type": "exampleTags", "id": str(tag.id)}]},
        ),
    )

    for method, path, document in requests:
        response = client.request(method, path, headers=headers, json=document)
        assert response.status_code == 401, (method, path, response.text)
        assert response.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"


def test_active_bearer_can_create_example(
    app: FastAPI,
    client: TestClient,
    committed_session: Session,
) -> None:
    user = _persist_user(committed_session)

    response = client.post(
        "/api/v1/examples",
        headers={
            "Accept": JSONAPI_MEDIA_TYPE,
            "Content-Type": JSONAPI_MEDIA_TYPE,
            "Authorization": f"Bearer {_access_token(app, user)}",
        },
        json=_create_document(),
    )

    assert response.status_code == 201
    assert response.json()["data"]["attributes"]["title"] == "보호된 생성"


def test_inactive_bearer_cannot_write_examples(
    app: FastAPI,
    client: TestClient,
    committed_session: Session,
) -> None:
    user = _persist_user(committed_session, is_active=False)

    response = client.post(
        "/api/v1/examples",
        headers={
            "Accept": JSONAPI_MEDIA_TYPE,
            "Content-Type": JSONAPI_MEDIA_TYPE,
            "Authorization": f"Bearer {_access_token(app, user)}",
        },
        json=_create_document(),
    )

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "USER_INACTIVE"


def test_example_openapi_protects_only_write_operations(app: FastAPI) -> None:
    schema = app.openapi()
    paths = schema["paths"]
    protected = (
        ("/api/v1/examples", "post"),
        ("/api/v1/examples/{resource_id}", "patch"),
        ("/api/v1/examples/{resource_id}", "put"),
        ("/api/v1/examples/{resource_id}", "delete"),
        ("/api/v1/examples/{resource_id}/relationships/category", "patch"),
        ("/api/v1/examples/{resource_id}/relationships/tags", "post"),
        ("/api/v1/examples/{resource_id}/relationships/tags", "patch"),
        ("/api/v1/examples/{resource_id}/relationships/tags", "delete"),
    )
    public = (
        ("/api/v1/examples", "get"),
        ("/api/v1/examples/{resource_id}", "get"),
        ("/api/v1/examples/{resource_id}/relationships/category", "get"),
        ("/api/v1/examples/{resource_id}/category", "get"),
        ("/api/v1/examples/{resource_id}/relationships/tags", "get"),
        ("/api/v1/examples/{resource_id}/tags", "get"),
    )

    for path, method in protected:
        operation = paths[path][method]
        assert operation["security"] == [{"BearerAuth": []}]
        assert set(operation["responses"]) >= {"401", "403"}
    for path, method in public:
        assert "security" not in paths[path][method]


def test_auth_lookup_and_crud_use_distinct_sessions_without_transaction_collision(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    user = _persist_user(committed_session)
    application = create_app()
    settings = application.state.auth_settings
    assert isinstance(settings, AuthSettings)
    session_ids: dict[str, list[int]] = {"auth": [], "crud": []}
    auth_transaction_states: list[bool] = []

    def override_auth_session() -> Iterator[Session]:
        with Session(bind=db_engine, expire_on_commit=False) as session:
            session_ids["auth"].append(id(session))
            yield session
            auth_transaction_states.append(session.in_transaction())

    def override_crud_session() -> Iterator[Session]:
        with Session(bind=db_engine, expire_on_commit=False) as session:
            session_ids["crud"].append(id(session))
            yield session

    application.dependency_overrides[get_auth_session] = override_auth_session
    application.dependency_overrides[get_session] = override_crud_session
    token = create_token(user.id, token_type="access", settings=settings)

    with TestClient(application, raise_server_exceptions=False) as transaction_client:
        response = transaction_client.post(
            "/api/v1/examples",
            headers={
                "Accept": JSONAPI_MEDIA_TYPE,
                "Content-Type": JSONAPI_MEDIA_TYPE,
                "Authorization": f"Bearer {token}",
            },
            json=_create_document(title="독립 세션 생성"),
        )

    assert response.status_code == 201
    assert session_ids["auth"] and session_ids["crud"]
    assert session_ids["auth"][0] != session_ids["crud"][0]
    assert auth_transaction_states == [True]

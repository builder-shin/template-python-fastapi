"""Current authenticated user JSON:API controller tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.jsonapi import JSONAPI_MEDIA_TYPE
from app.models import User
from config.auth import AuthSettings
from config.database import get_auth_session, get_session
from config.main import create_app

PASSWORD = "current-user-password"  # pragma: allowlist secret


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


def _persist_user(session: Session) -> User:
    user = User(
        email="me@example.com",
        password_hash=hash_password(PASSWORD),
    )
    session.add(user)
    session.commit()
    return user


def test_me_returns_only_the_public_user_document(
    app: FastAPI,
    client: TestClient,
    committed_session: Session,
) -> None:
    user = _persist_user(committed_session)
    settings = app.state.auth_settings
    assert isinstance(settings, AuthSettings)
    token = create_token(user.id, token_type="access", settings=settings)

    response = client.get(
        "/api/v1/users/me",
        headers={"Accept": JSONAPI_MEDIA_TYPE, "Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json()["data"] == {
        "type": "users",
        "id": str(user.id),
        "attributes": {
            "email": "me@example.com",
            "isActive": True,
            "createdAt": user.created_at.isoformat(),
            "updatedAt": user.updated_at.isoformat(),
        },
        "links": {"self": "/api/v1/users/me"},
    }
    assert "password" not in response.text.lower()


def test_me_rejects_invalid_token(client: TestClient) -> None:
    response = client.get(
        "/api/v1/users/me",
        headers={"Accept": JSONAPI_MEDIA_TYPE, "Authorization": "Bearer invalid"},
    )

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "INVALID_TOKEN"


def test_me_openapi_declares_bearer_and_jsonapi_auth_errors(app: FastAPI) -> None:
    schema = app.openapi()
    operation = schema["paths"]["/api/v1/users/me"]["get"]

    assert schema["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert operation["security"] == [{"BearerAuth": []}]
    assert set(operation["responses"]) >= {"200", "401"}
    assert "403" not in operation["responses"]
    assert set(operation["responses"]["401"]["content"]) == {JSONAPI_MEDIA_TYPE}

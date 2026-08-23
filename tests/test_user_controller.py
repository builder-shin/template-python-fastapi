"""Current authenticated user JSON:API controller tests."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.jsonapi import JSONAPI_MEDIA_TYPE
from app.models import User


def test_me_returns_only_the_public_user_document(
    app: FastAPI,
    client: TestClient,
    committed_session: Session,
    persisted_user: Callable[..., User],
    access_token: Callable[[FastAPI, User], str],
) -> None:
    user = persisted_user(committed_session, email="me@example.com")
    token = access_token(app, user)

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
    assert operation["responses"]["401"]["description"] == "Authentication required"
    assert operation["responses"]["500"]["description"] == "Internal server error"

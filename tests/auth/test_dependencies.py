"""Bearer authentication dependency integration tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import jwt
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

PASSWORD = "dependency-test-password"  # pragma: allowlist secret


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


def _persist_user(session: Session, *, is_active: bool = True) -> User:
    user = User(
        email="dependency@example.com",
        password_hash=hash_password(PASSWORD),
        is_active=is_active,
    )
    session.add(user)
    session.commit()
    return user


def _access_token(app: FastAPI, user: User) -> str:
    settings = app.state.auth_settings
    assert isinstance(settings, AuthSettings)
    return create_token(user.id, token_type="access", settings=settings)


def _get_me(client: TestClient, token: str | None = None, *, authorization: str | None = None):  # type: ignore[no-untyped-def]
    headers = {"Accept": JSONAPI_MEDIA_TYPE}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if authorization is not None:
        headers["Authorization"] = authorization
    return client.get("/api/v1/users/me", headers=headers)


def test_missing_authorization_is_authentication_required(client: TestClient) -> None:
    response = _get_me(client)

    assert response.status_code == 401
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"
    assert response.json()["errors"][0]["source"] == {"header": "Authorization"}


@pytest.mark.parametrize("authorization", ["Basic abc", "Bearer", "Bearer ", "Bearer one two"])
def test_malformed_bearer_is_invalid_token(
    client: TestClient,
    authorization: str,
) -> None:
    response = _get_me(client, authorization=authorization)

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "INVALID_TOKEN"
    assert response.json()["errors"][0]["source"] == {"header": "Authorization"}


def test_refresh_token_cannot_authenticate_an_access_route(
    app: FastAPI,
    client: TestClient,
    committed_session: Session,
) -> None:
    user = _persist_user(committed_session)
    settings = app.state.auth_settings
    assert isinstance(settings, AuthSettings)
    token = create_token(user.id, token_type="refresh", settings=settings)

    response = _get_me(client, token)

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "INVALID_TOKEN"


def test_invalid_claims_are_invalid_token(
    app: FastAPI,
    client: TestClient,
    committed_session: Session,
) -> None:
    user = _persist_user(committed_session)
    settings = app.state.auth_settings
    assert isinstance(settings, AuthSettings)
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(user.id),
            "jti": "not-a-uuid",
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "iss": settings.issuer,
            "aud": settings.audience,
        },
        settings.secret_key,
        algorithm="HS256",
    )

    response = _get_me(client, token)

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "INVALID_TOKEN"


def test_expired_access_token_is_token_expired(
    app: FastAPI,
    client: TestClient,
    committed_session: Session,
) -> None:
    user = _persist_user(committed_session)
    settings = app.state.auth_settings
    assert isinstance(settings, AuthSettings)
    token = create_token(
        user.id,
        token_type="access",
        settings=settings,
        now=datetime.now(UTC) - timedelta(seconds=settings.access_expires_seconds + 1),
    )

    response = _get_me(client, token)

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "TOKEN_EXPIRED"


def test_deleted_user_token_is_invalid_token(
    app: FastAPI,
    client: TestClient,
    committed_session: Session,
) -> None:
    user = _persist_user(committed_session)
    token = _access_token(app, user)
    committed_session.delete(user)
    committed_session.commit()

    response = _get_me(client, token)

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "INVALID_TOKEN"


def test_inactive_user_is_returned_by_current_user_dependency(
    app: FastAPI,
    client: TestClient,
    committed_session: Session,
) -> None:
    user = _persist_user(committed_session, is_active=False)

    response = _get_me(client, _access_token(app, user))

    assert response.status_code == 200
    assert response.json()["data"]["id"] == str(user.id)
    assert response.json()["data"]["attributes"]["isActive"] is False

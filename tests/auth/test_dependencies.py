"""Bearer authentication dependency integration tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.tokens import create_token
from app.jsonapi import JSONAPI_MEDIA_TYPE
from app.models import User
from config.auth import AuthSettings

# Module level so the probe route below does not trip ruff B008 (call in a default).
_PROBE_CURRENT_USER = Depends(get_current_user)


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
    client: TestClient,
    committed_session: Session,
    persisted_user: Callable[..., User],
    auth_settings: AuthSettings,
) -> None:
    user = persisted_user(committed_session)
    token = create_token(user.id, token_type="refresh", settings=auth_settings)

    response = _get_me(client, token)

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "INVALID_TOKEN"


def test_invalid_claims_are_invalid_token(
    client: TestClient,
    committed_session: Session,
    persisted_user: Callable[..., User],
    auth_settings: AuthSettings,
) -> None:
    user = persisted_user(committed_session)
    settings = auth_settings
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
    client: TestClient,
    committed_session: Session,
    persisted_user: Callable[..., User],
    auth_settings: AuthSettings,
) -> None:
    user = persisted_user(committed_session)
    settings = auth_settings
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
    persisted_user: Callable[..., User],
    access_token: Callable[[FastAPI, User], str],
) -> None:
    user = persisted_user(committed_session)
    token = access_token(app, user)
    committed_session.delete(user)
    committed_session.commit()

    response = _get_me(client, token)

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "INVALID_TOKEN"


def test_inactive_user_is_returned_by_current_user_dependency(
    app: FastAPI,
    client: TestClient,
    committed_session: Session,
    persisted_user: Callable[..., User],
    access_token: Callable[[FastAPI, User], str],
) -> None:
    user = persisted_user(committed_session, is_active=False)

    response = _get_me(client, access_token(app, user))

    assert response.status_code == 200
    assert response.json()["data"]["id"] == str(user.id)
    assert response.json()["data"]["attributes"]["isActive"] is False


def test_auth_lookup_session_is_closed_before_the_endpoint_runs(
    app_factory: Callable[..., FastAPI],
    db_engine: Engine,
    committed_session: Session,
    persisted_user: Callable[..., User],
    access_token: Callable[[FastAPI, User], str],
) -> None:
    """The auth session must release its pool connection before the endpoint body."""

    user = persisted_user(committed_session)
    auth_sessions: list[Session] = []
    observed_inside_endpoint: list[bool] = []

    def override_auth_session_factory() -> Callable[[], Session]:
        def build_session() -> Session:
            session = Session(bind=db_engine, expire_on_commit=False)
            auth_sessions.append(session)
            return session

        return build_session

    application = app_factory(auth_session_factory_override=override_auth_session_factory)

    # The ordering is only observable from INSIDE the endpoint body. Every assertion made
    # after the request passes just as well against a generator dependency that holds the
    # auth session open across the endpoint, which is exactly the regression this name
    # claims to guard, so the probe route samples the session state at the right moment.
    @application.get("/_probe/auth-session", include_in_schema=False)
    def probe_auth_session(current_user: User = _PROBE_CURRENT_USER) -> dict[str, str]:
        observed_inside_endpoint.append(auth_sessions[-1].in_transaction())
        return {"id": str(current_user.id)}

    token = access_token(application, user)

    with TestClient(application, raise_server_exceptions=False) as detached_client:
        probe_response = detached_client.get(
            "/_probe/auth-session",
            headers={"Authorization": f"Bearer {token}"},
        )
        response = _get_me(detached_client, token)

    assert probe_response.status_code == 200
    assert probe_response.json() == {"id": str(user.id)}
    # Closed BEFORE the endpoint body ran, not merely by the end of the request.
    assert observed_inside_endpoint == [False]

    assert response.status_code == 200
    assert len(auth_sessions) == 2
    assert [session.in_transaction() for session in auth_sessions] == [False, False]
    attributes = response.json()["data"]["attributes"]
    assert attributes["email"] == user.email
    assert attributes["isActive"] is True

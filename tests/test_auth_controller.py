"""Production authentication JSON:API controller integration tests."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from typing import cast
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.auth.passwords import DUMMY_PASSWORD_HASH, hash_password, verify_password
from app.auth.refresh_sessions import issue_token_pair
from app.auth.tokens import create_token, decode_token, hash_refresh_token
from app.jsonapi import JSONAPI_MEDIA_TYPE
from app.models import RefreshSession, User
from config.auth import AuthSettings
from config.database import get_session
from config.main import create_app

PASSWORD = "correct horse battery staple"  # pragma: allowlist secret
WRONG_PASSWORD = "incorrect horse battery staple"  # pragma: allowlist secret


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


def _register_document(
    *,
    email: str = "person@example.com",
    password: str = PASSWORD,
    resource_type: str = "users",
) -> dict[str, object]:
    return {
        "data": {
            "type": resource_type,
            "attributes": {"email": email, "password": password},
        }
    }


def _login_document(
    *,
    email: str = "person@example.com",
    password: str = PASSWORD,
) -> dict[str, object]:
    return {
        "data": {
            "type": "authCredentials",
            "attributes": {"email": email, "password": password},
        }
    }


def _refresh_document(refresh_token: str) -> dict[str, object]:
    return {
        "data": {
            "type": "refreshTokens",
            "attributes": {"refreshToken": refresh_token},
        }
    }


def _post_jsonapi(
    client: TestClient,
    path: str,
    document: dict[str, object],
    *,
    accept_language: str | None = None,
):  # type: ignore[no-untyped-def]
    headers = {"Accept": JSONAPI_MEDIA_TYPE, "Content-Type": JSONAPI_MEDIA_TYPE}
    if accept_language is not None:
        headers["Accept-Language"] = accept_language
    return client.post(path, headers=headers, json=document)


def _persist_user(
    session: Session,
    *,
    email: str = "person@example.com",
    password: str = PASSWORD,
    is_active: bool = True,
) -> User:
    user = User(
        email=email,
        password_hash=hash_password(password),
        is_active=is_active,
    )
    session.add(user)
    session.commit()
    return user


def test_register_normalizes_email_hashes_password_and_returns_public_user(
    client: TestClient,
    committed_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    response = _post_jsonapi(
        client,
        "/api/v1/auth/register",
        _register_document(email="  Person@Example.COM  "),
    )

    assert response.status_code == 201
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.headers["location"] == "/api/v1/users/me"
    resource = response.json()["data"]
    assert resource["type"] == "users"
    assert resource["attributes"]["email"] == "person@example.com"
    assert resource["attributes"]["isActive"] is True
    assert resource["links"] == {"self": "/api/v1/users/me"}

    user = committed_session.scalar(select(User).where(User.email == "person@example.com"))
    assert user is not None
    assert user.password_hash.startswith("$argon2")
    assert verify_password(PASSWORD, user.password_hash) is True
    assert PASSWORD not in response.text
    assert "passwordHash" not in response.text
    assert "password_hash" not in response.text
    assert PASSWORD not in caplog.text
    assert user.password_hash not in caplog.text


def test_register_normalizes_and_hashes_inside_the_caller_transaction(
    committed_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.controllers.api.v1 import auth_controller

    application = create_app()

    def override_session() -> Iterator[Session]:
        yield committed_session

    application.dependency_overrides[get_session] = override_session
    transaction_states: list[bool] = []
    real_normalize_email = auth_controller.normalize_email
    real_hash_password = auth_controller.hash_password

    def recording_normalize_email(email: str) -> str:
        transaction_states.append(committed_session.in_transaction())
        return real_normalize_email(email)

    def recording_hash_password(password: str) -> str:
        transaction_states.append(committed_session.in_transaction())
        return real_hash_password(password)

    monkeypatch.setattr(auth_controller, "normalize_email", recording_normalize_email)
    monkeypatch.setattr(auth_controller, "hash_password", recording_hash_password)

    with TestClient(application, raise_server_exceptions=False) as transaction_client:
        response = _post_jsonapi(
            transaction_client,
            "/api/v1/auth/register",
            _register_document(),
        )

    assert response.status_code == 201
    assert transaction_states == [True, True]


@pytest.mark.parametrize(
    ("document", "pointer"),
    [
        (_register_document(resource_type="authCredentials"), "/data/type"),
        (
            {
                "data": {
                    "type": "users",
                    "attributes": {"email": "person@example.com"},
                }
            },
            "/data/attributes/password",
        ),
    ],
)
def test_register_rejects_invalid_documents_with_stable_jsonapi_pointer(
    client: TestClient,
    document: dict[str, object],
    pointer: str,
) -> None:
    response = _post_jsonapi(
        client,
        "/api/v1/auth/register",
        document,
        accept_language="en",
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json()["errors"][0] == {
        "status": "422",
        "code": "VALIDATION_ERROR",
        "title": "Invalid request",
        "detail": "A request value failed validation.",
        "source": {"pointer": pointer},
    }


def test_register_rejects_normalized_duplicate_email_without_leaking_secrets(
    client: TestClient,
    committed_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    original = _persist_user(committed_session)
    caplog.set_level(logging.DEBUG)

    response = _post_jsonapi(
        client,
        "/api/v1/auth/register",
        _register_document(email="  PERSON@EXAMPLE.COM  "),
        accept_language="ko",
    )

    assert response.status_code == 409
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json()["errors"][0] == {
        "status": "409",
        "code": "EMAIL_ALREADY_REGISTERED",
        "title": "이미 등록된 이메일",
        "detail": "이미 등록된 이메일입니다.",
    }
    assert committed_session.scalar(select(func.count()).select_from(User)) == 1
    assert PASSWORD not in response.text
    assert PASSWORD not in caplog.text
    assert original.password_hash not in caplog.text


def test_concurrent_registration_of_same_normalized_email_has_one_winner(
    app: FastAPI,
    concurrent_session_factory: Callable[[], Session],
) -> None:
    barrier = Barrier(2)

    def register(email: str) -> tuple[int, str | None]:
        with TestClient(app, raise_server_exceptions=False) as thread_client:
            barrier.wait()
            response = _post_jsonapi(
                thread_client,
                "/api/v1/auth/register",
                _register_document(email=email),
            )
            error_code = response.json().get("errors", [{}])[0].get("code")
            return response.status_code, cast(str | None, error_code)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(register, ["race@example.com", " RACE@EXAMPLE.COM "]))

    assert sorted(outcomes) == [(201, None), (409, "EMAIL_ALREADY_REGISTERED")]
    verification_session = concurrent_session_factory()
    assert verification_session.scalar(select(func.count()).select_from(User)) == 1


def test_login_returns_persisted_token_pair_with_strict_claims(
    client: TestClient,
    committed_session: Session,
    app: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = _persist_user(committed_session)
    caplog.set_level(logging.DEBUG)

    response = _post_jsonapi(
        client,
        "/api/v1/auth/login",
        _login_document(email=" PERSON@EXAMPLE.COM "),
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    resource = response.json()["data"]
    assert resource["type"] == "authTokens"
    assert resource["attributes"]["tokenType"] == "Bearer"
    assert resource["attributes"]["expiresIn"] == 900
    assert resource["attributes"]["refreshExpiresIn"] == 2_592_000

    settings = cast(AuthSettings, app.state.auth_settings)
    access_token = resource["attributes"]["accessToken"]
    refresh_token = resource["attributes"]["refreshToken"]
    access_claims = decode_token(access_token, expected_type="access", settings=settings)
    refresh_claims = decode_token(refresh_token, expected_type="refresh", settings=settings)
    assert access_claims.sub == str(user.id)
    assert refresh_claims.sub == str(user.id)
    assert int((access_claims.exp - access_claims.iat).total_seconds()) == 900
    assert int((refresh_claims.exp - refresh_claims.iat).total_seconds()) == 2_592_000
    assert resource["id"] == str(refresh_claims.jti)

    committed_session.expire_all()
    refresh_session = committed_session.get(RefreshSession, refresh_claims.jti)
    assert refresh_session is not None
    assert refresh_session.user_id == user.id
    assert refresh_session.token_hash == hash_refresh_token(refresh_token)
    assert refresh_session.token_hash != refresh_token
    assert refresh_session.expires_at == refresh_claims.exp
    assert PASSWORD not in response.text
    assert user.password_hash not in response.text
    assert PASSWORD not in caplog.text
    assert user.password_hash not in caplog.text


def test_login_uses_dummy_hash_and_hides_email_existence(
    client: TestClient,
    committed_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _persist_user(committed_session)
    from app.controllers.api.v1 import auth_controller

    verified_hashes: list[str] = []
    real_verify_password = auth_controller.verify_password

    def recording_verify(password: str, password_hash: str) -> bool:
        verified_hashes.append(password_hash)
        return real_verify_password(password, password_hash)

    monkeypatch.setattr(auth_controller, "verify_password", recording_verify)

    missing_response = _post_jsonapi(
        client,
        "/api/v1/auth/login",
        _login_document(email="missing@example.com", password=WRONG_PASSWORD),
        accept_language="en",
    )
    wrong_response = _post_jsonapi(
        client,
        "/api/v1/auth/login",
        _login_document(password=WRONG_PASSWORD),
        accept_language="en",
    )

    assert missing_response.status_code == wrong_response.status_code == 401
    assert missing_response.headers["content-type"] == wrong_response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert missing_response.json() == wrong_response.json()
    assert missing_response.json()["errors"][0] == {
        "status": "401",
        "code": "INVALID_CREDENTIALS",
        "title": "Invalid credentials",
        "detail": "The email or password is incorrect.",
    }
    assert verified_hashes == [DUMMY_PASSWORD_HASH, user.password_hash]


def test_login_rejects_inactive_user_in_korean(
    client: TestClient,
    committed_session: Session,
) -> None:
    _persist_user(committed_session, is_active=False)

    response = _post_jsonapi(
        client,
        "/api/v1/auth/login",
        _login_document(),
        accept_language="ko",
    )

    assert response.status_code == 403
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json()["errors"][0] == {
        "status": "403",
        "code": "USER_INACTIVE",
        "title": "비활성 사용자",
        "detail": "사용자 계정이 비활성 상태입니다.",
    }
    assert committed_session.scalar(select(func.count()).select_from(RefreshSession)) == 0


def test_login_rechecks_active_state_after_concurrent_deactivation(
    app: FastAPI,
    concurrent_session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_session = concurrent_session_factory()
    user = _persist_user(setup_session)
    setup_session.close()
    password_checked = Event()
    allow_login_to_lock = Event()
    from app.controllers.api.v1 import auth_controller

    real_verify_password = auth_controller.verify_password

    def pause_after_password_check(password: str, password_hash: str) -> bool:
        matches = real_verify_password(password, password_hash)
        password_checked.set()
        if not allow_login_to_lock.wait(timeout=10):
            raise TimeoutError("login lock permission timed out")
        return matches

    monkeypatch.setattr(auth_controller, "verify_password", pause_after_password_check)

    def login():  # type: ignore[no-untyped-def]
        with TestClient(app, raise_server_exceptions=False) as thread_client:
            return _post_jsonapi(
                thread_client,
                "/api/v1/auth/login",
                _login_document(),
                accept_language="en",
            )

    with ThreadPoolExecutor(max_workers=1) as executor:
        login_future = executor.submit(login)
        assert password_checked.wait(timeout=10)
        with concurrent_session_factory() as deactivation_session, deactivation_session.begin():
            current_user = deactivation_session.get(User, user.id)
            assert current_user is not None
            current_user.is_active = False
        allow_login_to_lock.set()
        response = login_future.result(timeout=10)

    assert response.status_code == 403
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json()["errors"][0]["code"] == "USER_INACTIVE"
    with concurrent_session_factory() as verification_session:
        assert verification_session.scalar(select(func.count()).select_from(RefreshSession)) == 0


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
    ],
)
def test_auth_write_routes_enforce_accept_and_content_type(client: TestClient, path: str) -> None:
    if path.endswith("register"):
        document = _register_document()
    elif path.endswith("login"):
        document = _login_document()
    else:
        document = _refresh_document("not-a-jwt")

    unacceptable = client.post(
        path,
        headers={"Accept": "application/xml", "Content-Type": JSONAPI_MEDIA_TYPE},
        json=document,
    )
    unsupported = client.post(
        path,
        headers={"Accept": JSONAPI_MEDIA_TYPE, "Content-Type": "application/json"},
        json=document,
    )

    assert unacceptable.status_code == 406
    assert unacceptable.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert unacceptable.json()["errors"][0]["code"] == "NOT_ACCEPTABLE"
    assert unacceptable.json()["errors"][0]["source"] == {"header": "Accept"}
    assert unsupported.status_code == 415
    assert unsupported.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert unsupported.json()["errors"][0]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert unsupported.json()["errors"][0]["source"] == {"header": "Content-Type"}


def test_issue_token_pair_leaves_commit_to_the_caller(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.auth.refresh_sessions import issue_token_pair

    user = User(email="transaction@example.com", password_hash=hash_password(PASSWORD))
    db_session.add(user)
    db_session.flush()

    def forbidden_commit(self: Session) -> None:
        raise AssertionError("issue_token_pair must not commit")

    monkeypatch.setattr(Session, "commit", forbidden_commit)

    token_pair = issue_token_pair(
        db_session,
        user,
        AuthSettings(secret_key="s" * 64),  # pragma: allowlist secret
    )
    db_session.flush()

    assert db_session.get(RefreshSession, token_pair.id) is not None
    assert db_session.in_transaction() is True


def test_refresh_rotates_token_and_persists_the_replacement(
    client: TestClient,
    committed_session: Session,
    app: FastAPI,
) -> None:
    user = _persist_user(committed_session)
    login_response = _post_jsonapi(client, "/api/v1/auth/login", _login_document())
    original_resource = login_response.json()["data"]
    original_token = original_resource["attributes"]["refreshToken"]
    original_id = original_resource["id"]

    response = _post_jsonapi(
        client,
        "/api/v1/auth/refresh",
        _refresh_document(original_token),
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    resource = response.json()["data"]
    assert resource["type"] == "authTokens"
    assert resource["id"] != original_id
    settings = cast(AuthSettings, app.state.auth_settings)
    claims = decode_token(
        resource["attributes"]["refreshToken"],
        expected_type="refresh",
        settings=settings,
    )
    assert claims.sub == str(user.id)

    committed_session.expire_all()
    old_row = committed_session.get(RefreshSession, original_id)
    new_row = committed_session.get(RefreshSession, claims.jti)
    assert old_row is not None
    assert old_row.revoked_at is not None
    assert old_row.replaced_by_id == claims.jti
    assert new_row is not None
    assert new_row.revoked_at is None


def test_reusing_rotated_token_revokes_only_that_users_active_sessions(
    client: TestClient,
    committed_session: Session,
) -> None:
    first_user = _persist_user(committed_session)
    other_user = _persist_user(
        committed_session,
        email="other@example.com",
    )
    first_login = _post_jsonapi(client, "/api/v1/auth/login", _login_document()).json()["data"]
    second_login = _post_jsonapi(client, "/api/v1/auth/login", _login_document()).json()["data"]
    other_login = _post_jsonapi(
        client,
        "/api/v1/auth/login",
        _login_document(email="other@example.com"),
    ).json()["data"]
    original_token = first_login["attributes"]["refreshToken"]

    rotated = _post_jsonapi(
        client,
        "/api/v1/auth/refresh",
        _refresh_document(original_token),
    )
    reused = _post_jsonapi(
        client,
        "/api/v1/auth/refresh",
        _refresh_document(original_token),
        accept_language="en",
    )

    assert rotated.status_code == 200
    assert reused.status_code == 401
    assert reused.json()["errors"][0]["code"] == "TOKEN_REVOKED"

    committed_session.expire_all()
    first_active = committed_session.scalar(
        select(func.count())
        .select_from(RefreshSession)
        .where(RefreshSession.user_id == first_user.id, RefreshSession.revoked_at.is_(None))
    )
    other_active = committed_session.scalar(
        select(func.count())
        .select_from(RefreshSession)
        .where(RefreshSession.user_id == other_user.id, RefreshSession.revoked_at.is_(None))
    )
    assert first_active == 0
    assert other_active == 1
    assert second_login["id"] != first_login["id"]
    assert other_login["id"] != first_login["id"]


def test_inactive_user_refresh_revokes_session_before_returning_403(
    client: TestClient,
    committed_session: Session,
) -> None:
    user = _persist_user(committed_session)
    resource = _post_jsonapi(client, "/api/v1/auth/login", _login_document()).json()["data"]
    user.is_active = False
    committed_session.commit()

    response = _post_jsonapi(
        client,
        "/api/v1/auth/refresh",
        _refresh_document(resource["attributes"]["refreshToken"]),
        accept_language="ko",
    )

    assert response.status_code == 403
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json()["errors"][0]["code"] == "USER_INACTIVE"
    committed_session.expire_all()
    assert committed_session.get(RefreshSession, resource["id"]).revoked_at is not None  # type: ignore[union-attr]


def test_concurrent_refresh_has_one_rotation_then_reuse_revokes_it(
    app: FastAPI,
    concurrent_session_factory: Callable[[], Session],
) -> None:
    setup_session = concurrent_session_factory()
    settings = cast(AuthSettings, app.state.auth_settings)
    with setup_session.begin():
        user = User(
            email="refresh-race@example.com",
            password_hash=hash_password(PASSWORD),
        )
        setup_session.add(user)
        setup_session.flush()
        original = issue_token_pair(setup_session, user, settings)
    setup_session.close()
    barrier = Barrier(2)

    def refresh() -> tuple[int, str | None]:
        with TestClient(app, raise_server_exceptions=False) as thread_client:
            barrier.wait()
            response = _post_jsonapi(
                thread_client,
                "/api/v1/auth/refresh",
                _refresh_document(original.refresh_token),
            )
            error_code = response.json().get("errors", [{}])[0].get("code")
            return response.status_code, cast(str | None, error_code)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: refresh(), range(2)))

    assert sorted(outcomes) == [(200, None), (401, "TOKEN_REVOKED")]
    verification_session = concurrent_session_factory()
    rows = list(verification_session.scalars(select(RefreshSession).where(RefreshSession.user_id == user.id)))
    assert len(rows) == 2
    assert sum(row.replaced_by_id is not None for row in rows) == 1
    assert all(row.revoked_at is not None for row in rows)


def test_logout_is_empty_idempotent_and_rejects_invalid_token(
    client: TestClient,
    committed_session: Session,
) -> None:
    user = _persist_user(committed_session)
    resource = _post_jsonapi(client, "/api/v1/auth/login", _login_document()).json()["data"]
    raw_token = resource["attributes"]["refreshToken"]

    first = _post_jsonapi(client, "/api/v1/auth/logout", _refresh_document(raw_token))
    second = _post_jsonapi(client, "/api/v1/auth/logout", _refresh_document(raw_token))
    invalid = _post_jsonapi(
        client,
        "/api/v1/auth/logout",
        _refresh_document("not-a-jwt"),
        accept_language="en",
    )

    assert first.status_code == second.status_code == 204
    assert first.content == second.content == b""
    assert "content-type" not in first.headers
    assert invalid.status_code == 401
    assert invalid.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert invalid.json()["errors"][0]["code"] == "INVALID_TOKEN"
    committed_session.expire_all()
    assert committed_session.get(RefreshSession, resource["id"]).revoked_at is not None  # type: ignore[union-attr]
    assert user.id is not None


def test_logout_expired_token_commits_revocation_before_error(
    client: TestClient,
    committed_session: Session,
    app: FastAPI,
) -> None:
    user = _persist_user(committed_session)
    settings = cast(AuthSettings, app.state.auth_settings)
    issued_at = datetime.now(UTC) - timedelta(days=31)
    jti = uuid4()
    raw_token = create_token(
        user.id,
        token_type="refresh",
        settings=settings,
        jti=jti,
        now=issued_at,
    )
    committed_session.add(
        RefreshSession(
            id=jti,
            user_id=user.id,
            token_hash=hash_refresh_token(raw_token),
            expires_at=issued_at + timedelta(seconds=settings.refresh_expires_seconds),
        )
    )
    committed_session.commit()

    response = _post_jsonapi(
        client,
        "/api/v1/auth/logout",
        _refresh_document(raw_token),
        accept_language="en",
    )

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "TOKEN_EXPIRED"
    committed_session.expire_all()
    assert committed_session.get(RefreshSession, jti).revoked_at is not None  # type: ignore[union-attr]


def test_logout_openapi_documents_empty_204_and_jsonapi_errors(app: FastAPI) -> None:
    responses = app.openapi()["paths"]["/api/v1/auth/logout"]["post"]["responses"]

    assert "content" not in responses["204"]
    for status_code in ("401", "406", "415", "422", "500"):
        assert set(responses[status_code]["content"]) == {JSONAPI_MEDIA_TYPE}

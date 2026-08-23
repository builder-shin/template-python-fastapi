"""Contract tests for the shared application, client and authentication fixtures.

These pin ``tests/conftest.py`` itself: every controller test relies on the
fixtures below behaving exactly this way, and nothing else in the suite asserts
on them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.jsonapi import JSONAPI_MEDIA_TYPE, JsonApiException
from app.models import ExampleCategory, User
from config.auth import AuthSettings
from config.database import get_auth_session_factory, get_session

from .conftest import AUTHENTICATED_EMAIL

_SESSION_DEPENDENCY = Depends(get_session)


def _example_document(title: str = "fixture 예시") -> dict[str, object]:
    return {
        "data": {
            "type": "examples",
            "attributes": {
                "title": title,
                "description": None,
                "status": "active",
                "score": 70,
            },
        }
    }


def test_app_fixture_overrides_both_session_dependencies(app: FastAPI) -> None:
    """Both database entry points must point at the test engine, not just one.

    ``get_session`` feeds endpoint bodies and ``get_auth_session_factory`` feeds
    the bearer lookup. Overriding only one leaves the other resolving through the
    session factory ``create_app()`` built, so authentication and the endpoint
    body would observe different engines.
    """

    assert set(app.dependency_overrides) == {get_session, get_auth_session_factory}


def test_app_fixture_resolves_auth_lookup_against_the_test_database(
    app: FastAPI,
    client: TestClient,
    committed_session: Session,
    persisted_user: Callable[..., User],
    access_token: Callable[[FastAPI, User], str],
) -> None:
    """Behavioural twin of the override pin: the bearer lookup sees test rows."""

    user = persisted_user(committed_session)

    response = client.get(
        "/api/v1/users/me",
        headers={"Accept": JSONAPI_MEDIA_TYPE, "Authorization": f"Bearer {access_token(app, user)}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["id"] == str(user.id)


def test_client_fixture_renders_server_errors_instead_of_raising(
    app: FastAPI,
    client: TestClient,
) -> None:
    """``raise_server_exceptions=False`` is part of the fixture contract."""

    @app.get("/fixture-boom")
    def boom() -> None:
        raise RuntimeError("fixture-private-detail")

    response = client.get("/fixture-boom")

    assert response.status_code == 500
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json()["errors"][0]["code"] == "INTERNAL_SERVER_ERROR"
    assert "fixture-private-detail" not in response.text


def test_jsonapi_headers_is_a_fresh_mutable_read_and_write_dict(
    jsonapi_headers: dict[str, str],
    client: TestClient,
) -> None:
    assert jsonapi_headers == {"Accept": JSONAPI_MEDIA_TYPE, "Content-Type": JSONAPI_MEDIA_TYPE}

    jsonapi_headers["Authorization"] = "Bearer mutated-in-place"
    response = client.post("/api/v1/examples", headers=jsonapi_headers, json=_example_document())

    assert response.status_code not in {406, 415}


def test_jsonapi_headers_does_not_leak_mutations_between_tests(
    jsonapi_headers: dict[str, str],
) -> None:
    assert jsonapi_headers == {"Accept": JSONAPI_MEDIA_TYPE, "Content-Type": JSONAPI_MEDIA_TYPE}


def test_auth_settings_fixture_is_the_application_state_instance(
    app: FastAPI,
    auth_settings: AuthSettings,
) -> None:
    """Tokens must be minted with the verifier the application actually uses."""

    assert isinstance(auth_settings, AuthSettings)
    assert auth_settings is app.state.auth_settings


def test_persisted_user_factory_defaults_to_a_unique_active_argon2_user(
    committed_session: Session,
    persisted_user: Callable[..., User],
) -> None:
    first = persisted_user(committed_session)
    second = persisted_user(committed_session)

    assert first.email != second.email
    assert first.is_active is True
    assert first.password_hash.startswith("$argon2")


def test_persisted_user_factory_honours_explicit_email_and_inactive_flag(
    app: FastAPI,
    client: TestClient,
    committed_session: Session,
    jsonapi_headers: dict[str, str],
    persisted_user: Callable[..., User],
    access_token: Callable[[FastAPI, User], str],
) -> None:
    user = persisted_user(committed_session, email="explicit@example.com", is_active=False)

    assert user.email == "explicit@example.com"

    response = client.post(
        "/api/v1/examples",
        headers={**jsonapi_headers, "Authorization": f"Bearer {access_token(app, user)}"},
        json=_example_document(),
    )

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "USER_INACTIVE"


def test_authenticated_client_sends_bearer_and_leaves_the_plain_client_anonymous(
    authenticated_client: TestClient,
    authenticated_user: User,
    client: TestClient,
) -> None:
    """``authenticated_client`` must not mutate the shared anonymous ``client``."""

    assert authenticated_user.email == AUTHENTICATED_EMAIL

    authenticated = authenticated_client.get("/api/v1/users/me", headers={"Accept": JSONAPI_MEDIA_TYPE})
    anonymous = client.get("/api/v1/users/me", headers={"Accept": JSONAPI_MEDIA_TYPE})

    assert authenticated.status_code == 200
    assert authenticated.json()["data"]["id"] == str(authenticated_user.id)
    assert anonymous.status_code == 401
    assert anonymous.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"


def test_app_factory_installs_the_supplied_instrumented_overrides(
    app_factory: Callable[..., FastAPI],
    db_engine: Engine,
    committed_session: Session,
    persisted_user: Callable[..., User],
    access_token: Callable[[FastAPI, User], str],
    jsonapi_headers: dict[str, str],
) -> None:
    """Instrumented tests keep their own generators without copying the assembly."""

    auth_sessions: list[Session] = []
    crud_sessions: list[Session] = []

    def override_auth_session_factory() -> Callable[[], Session]:
        def build_session() -> Session:
            session = Session(bind=db_engine, expire_on_commit=False)
            auth_sessions.append(session)
            return session

        return build_session

    def override_crud_session() -> Iterator[Session]:
        with Session(bind=db_engine, expire_on_commit=False) as session:
            crud_sessions.append(session)
            yield session

    application = app_factory(
        session_override=override_crud_session,
        auth_session_factory_override=override_auth_session_factory,
    )

    assert application.dependency_overrides[get_session] is override_crud_session
    assert application.dependency_overrides[get_auth_session_factory] is override_auth_session_factory

    user = persisted_user(committed_session)
    with TestClient(application, raise_server_exceptions=False) as instrumented_client:
        response = instrumented_client.post(
            "/api/v1/examples",
            headers={**jsonapi_headers, "Authorization": f"Bearer {access_token(application, user)}"},
            json=_example_document(),
        )

    assert response.status_code == 201, response.text
    assert len(auth_sessions) == 1
    assert len(crud_sessions) == 1
    assert auth_sessions[0] is not crud_sessions[0]


def test_minimal_app_factory_registers_handlers_and_omits_the_auth_override(
    minimal_app_factory: Callable[..., FastAPI],
) -> None:
    """The minimal app gets JSON:API handlers and the test session, but no auth.

    A minimal app is for shared-concern regressions, whose routers carry no
    bearer dependency, so ``get_auth_session_factory`` is deliberately left
    un-overridden. A test that needs bearer authentication uses the ``app``
    fixture instead.
    """

    application = minimal_app_factory()

    @application.get("/fixture-not-found")
    def not_found() -> None:
        raise JsonApiException(status_code=404, code="RESOURCE_NOT_FOUND")

    assert set(application.dependency_overrides) == {get_session}

    with TestClient(application, raise_server_exceptions=False) as minimal_client:
        rejected = minimal_client.get("/fixture-not-found")

    assert rejected.status_code == 404
    assert rejected.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert rejected.json()["errors"][0]["code"] == "RESOURCE_NOT_FOUND"


def test_minimal_app_factory_can_skip_exception_handler_registration(
    minimal_app_factory: Callable[..., FastAPI],
) -> None:
    """``register_handlers=False`` keeps the deliberately bare-app regressions bare."""

    application = minimal_app_factory(register_handlers=False)

    assert set(application.dependency_overrides) == {get_session}
    assert JsonApiException not in application.exception_handlers


def test_minimal_app_factory_writes_through_the_supplied_session_factory(
    minimal_app_factory: Callable[..., FastAPI],
    db_engine: Engine,
    committed_session: Session,
) -> None:
    """``session_factory=`` lets a test hand out and observe its own sessions."""

    opened: list[Session] = []

    def open_recorded_session() -> Session:
        session = Session(bind=db_engine, expire_on_commit=False)
        opened.append(session)
        return session

    application = minimal_app_factory(session_factory=open_recorded_session)

    @application.post("/fixture-categories", status_code=201)
    def create_category(session: Session = _SESSION_DEPENDENCY) -> Response:
        session.add(ExampleCategory(name="fixture 분류"))
        session.commit()
        return Response(status_code=201)

    with TestClient(application, raise_server_exceptions=False) as minimal_client:
        response = minimal_client.post("/fixture-categories")

    assert response.status_code == 201
    assert len(opened) == 1
    assert committed_session.scalar(select(func.count()).select_from(ExampleCategory)) == 1

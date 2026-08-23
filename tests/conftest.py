"""Shared database, application, client and authentication fixtures.

The database fixtures activate only when explicitly requested. The application
fixtures below are the single place in the suite that assembles ``create_app()``
together with ``dependency_overrides``; see ``tests/AGENTS.md``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.jsonapi import JSONAPI_MEDIA_TYPE, register_exception_handlers
from app.models import Base, User
from config.auth import AuthSettings
from config.database import get_auth_session_factory, get_session
from config.main import create_app

os.environ.setdefault(
    "JWT_SECRET_KEY",
    "test-only-jwt-secret-key-at-least-32-bytes",  # pragma: allowlist secret
)

# DATABASE_URL is fail-closed, so every create_app() in the suite needs one. Point it at
# the test database when available so a developer DATABASE_URL can never be picked up,
# and otherwise at an unreachable placeholder because create_engine never connects.
_test_database_url = os.getenv("TEST_DATABASE_URL")
if _test_database_url:
    os.environ["DATABASE_URL"] = _test_database_url
else:
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://unused:unused@127.0.0.1:1/unused_test",  # pragma: allowlist secret
    )

# REDIS_URL is fail-closed too and importing app.jobs configures the broker, so give the
# suite an explicit value instead of relying on a production default.
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")

API_ROOT = Path(__file__).resolve().parents[1]


def _require_test_database_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.fail("TEST_DATABASE_URL is required for database tests")

    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        pytest.fail("database tests require a database name ending in '_test'")
    return database_url


def _alembic_config(database_url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _truncate_tables(engine: Engine) -> None:
    table_names = ", ".join(f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables))
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def test_database_url() -> str:
    return _require_test_database_url()


@pytest.fixture(scope="session")
def migrated_database(test_database_url: str) -> Iterator[str]:
    command.upgrade(_alembic_config(test_database_url), "head")
    yield test_database_url


@pytest.fixture(scope="session")
def db_engine(migrated_database: str) -> Iterator[Engine]:
    engine = create_engine(migrated_database, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def committed_session(db_engine: Engine) -> Iterator[Session]:
    _truncate_tables(db_engine)
    session = Session(bind=db_engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        _truncate_tables(db_engine)


@pytest.fixture
def concurrent_session_factory(db_engine: Engine) -> Iterator[Callable[[], Session]]:
    _truncate_tables(db_engine)
    sessions: list[Session] = []
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)

    def create_session() -> Session:
        session = factory()
        sessions.append(session)
        return session

    try:
        yield create_session
    finally:
        for session in sessions:
            session.close()
        _truncate_tables(db_engine)


AUTHENTICATED_EMAIL = "authenticated@example.com"
AUTHENTICATED_PASSWORD = "authenticated-fixture-password"  # pragma: allowlist secret

SessionOverride = Callable[[], Iterator[Session]]
AuthSessionFactoryOverride = Callable[[], Callable[[], Session]]


@pytest.fixture
def app_factory(db_engine: Engine) -> Callable[..., FastAPI]:
    """Build ``create_app()`` applications wired to the test engine.

    This is the only place in the suite that pairs ``create_app()`` with
    ``dependency_overrides``. Both database entry points are always overridden:
    ``get_session`` feeds every endpoint body, while ``get_auth_session_factory``
    feeds the short-lived bearer lookup in ``app.auth.dependencies``. Overriding
    only one of them leaves the other resolving against the session factory that
    ``create_app()`` built, which is a different engine than the test fixtures
    use, so writes and authentication would observe different databases.

    A test that needs to observe the sessions it hands out passes its own
    ``session_override`` / ``auth_session_factory_override`` instead of copying
    the assembly.
    """

    def build_app(
        *,
        session_override: SessionOverride | None = None,
        auth_session_factory_override: AuthSessionFactoryOverride | None = None,
    ) -> FastAPI:
        application = create_app()

        def override_session() -> Iterator[Session]:
            with Session(bind=db_engine, expire_on_commit=False) as session:
                yield session

        def override_auth_session_factory() -> Callable[[], Session]:
            return lambda: Session(bind=db_engine, expire_on_commit=False)

        application.dependency_overrides[get_session] = session_override or override_session
        application.dependency_overrides[get_auth_session_factory] = (
            auth_session_factory_override or override_auth_session_factory
        )
        return application

    return build_app


@pytest.fixture
def app(app_factory: Callable[..., FastAPI]) -> FastAPI:
    """Return the production application wired to the test database."""

    return app_factory()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """Return an anonymous client that renders server errors as JSON:API documents."""

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def jsonapi_headers() -> dict[str, str]:
    """Return a fresh JSON:API read+write header dict that callers may mutate."""

    return {"Accept": JSONAPI_MEDIA_TYPE, "Content-Type": JSONAPI_MEDIA_TYPE}


@pytest.fixture
def auth_settings(app: FastAPI) -> AuthSettings:
    """Return the settings the application itself verifies tokens with."""

    settings = app.state.auth_settings
    assert isinstance(settings, AuthSettings)
    return settings


@pytest.fixture
def persisted_user() -> Callable[..., User]:
    """Return a factory that commits a user with a unique email by default."""

    def create_user(
        session: Session,
        *,
        email: str | None = None,
        password: str = AUTHENTICATED_PASSWORD,
        is_active: bool = True,
    ) -> User:
        user = User(
            email=email or f"user-{uuid4()}@example.com",
            password_hash=hash_password(password),
            is_active=is_active,
        )
        session.add(user)
        session.commit()
        return user

    return create_user


@pytest.fixture
def access_token() -> Callable[[FastAPI, User], str]:
    """Return a factory minting access tokens against a given application.

    The application is an explicit argument so that a test observing its own
    instrumented application still mints tokens with the verifier that
    application actually uses, instead of a coincidentally identical one.
    """

    def mint(application: FastAPI, user: User) -> str:
        settings = application.state.auth_settings
        assert isinstance(settings, AuthSettings)
        return create_token(user.id, token_type="access", settings=settings)

    return mint


@pytest.fixture
def authenticated_user(
    committed_session: Session,
    persisted_user: Callable[..., User],
) -> User:
    """Return one active, committed user for bearer-authenticated requests."""

    return persisted_user(committed_session, email=AUTHENTICATED_EMAIL)


@pytest.fixture
def authenticated_client(
    app: FastAPI,
    access_token: Callable[[FastAPI, User], str],
    authenticated_user: User,
) -> Iterator[TestClient]:
    """Return a client that sends a bearer token for ``authenticated_user``.

    It deliberately builds its own client instead of mutating ``client`` so that a
    test may request both and still assert the anonymous 401 contract.
    """

    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client.headers["Authorization"] = f"Bearer {access_token(app, authenticated_user)}"
        yield test_client


@pytest.fixture
def minimal_app_factory(db_engine: Engine) -> Callable[..., FastAPI]:
    """Build the minimal apps that shared-concern regressions use.

    Only ``get_session`` is overridden: a minimal app registers no bearer
    dependency, so ``get_auth_session_factory`` is never resolved there.
    """

    def build_app(
        *routers: APIRouter,
        session_factory: Callable[[], Session] | None = None,
        register_handlers: bool = True,
    ) -> FastAPI:
        application = FastAPI()
        if register_handlers:
            register_exception_handlers(application)
        for router in routers:
            application.include_router(router)

        def open_session() -> Session:
            return Session(bind=db_engine, expire_on_commit=False)

        build_session = session_factory or open_session

        def override_session() -> Iterator[Session]:
            with build_session() as session:
                yield session

        application.dependency_overrides[get_session] = override_session
        return application

    return build_app

"""Synchronous SQLAlchemy database configuration."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import cast

from fastapi import Depends, Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import read_int, require_env


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """Database connection and pool settings."""

    url: str
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30

    def __post_init__(self) -> None:
        if self.pool_size < 1:
            raise ValueError("pool_size must be at least 1")
        if self.max_overflow < 0:
            raise ValueError("max_overflow must be at least 0")
        if self.pool_timeout <= 0:
            raise ValueError("pool_timeout must be greater than 0")

    @classmethod
    def from_env(cls) -> DatabaseSettings:
        """Build fail-closed settings from environment variables."""

        return cls(
            url=require_env("DATABASE_URL"),
            pool_size=read_int("DB_POOL_SIZE", 5),
            max_overflow=read_int("DB_MAX_OVERFLOW", 10),
            pool_timeout=read_int("DB_POOL_TIMEOUT", 30),
        )


def build_engine(settings: DatabaseSettings) -> Engine:
    """Build a synchronous SQLAlchemy engine with a bounded connection pool."""

    return create_engine(
        settings.url,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout,
        pool_pre_ping=True,
    )


def build_session_factory(settings: DatabaseSettings) -> sessionmaker[Session]:
    """Build a session factory bound to a freshly built engine."""

    return sessionmaker(bind=build_engine(settings), expire_on_commit=False)


_session_factory: sessionmaker[Session] | None = None
_session_factory_lock = threading.Lock()


def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory for worker and CLI entry points.

    The API builds its own engine in the application factory; this lazily built
    factory exists for processes without a FastAPI application, so importing this
    module never creates an engine on its own.

    A Dramatiq worker calls this from every one of its threads, and building the
    first engine is slow enough (the one-time psycopg DBAPI import) that a bare
    check-then-set hands each cold thread its own engine and its own pool. The
    double-checked lock keeps the fast path lock-free once the factory exists
    while making the construction happen exactly once.
    """

    global _session_factory
    if _session_factory is None:
        with _session_factory_lock:
            if _session_factory is None:
                _session_factory = build_session_factory(DatabaseSettings.from_env())
    return _session_factory


def _application_session_factory(request: Request) -> sessionmaker[Session]:
    return cast("sessionmaker[Session]", request.app.state.session_factory)


def get_session(request: Request) -> Iterator[Session]:
    """Yield a request-scoped session and close it after use."""

    with _application_session_factory(request)() as session:
        yield session


_SESSION_DEPENDENCY = Depends(get_session)


def get_request_session(request: Request, session: Session = _SESSION_DEPENDENCY) -> Session:
    """Return the request-scoped session after publishing it on ``request.state``.

    ``handle_integrity_error`` rolls back whatever session sits at
    ``request.state.session``, so every endpoint that writes resolves its session
    through this wrapper instead of repeating the binding by hand. It wraps
    ``get_session`` rather than binding inside it so that
    ``dependency_overrides[get_session]`` still replaces the underlying session.
    The authentication path deliberately keeps its own short-lived session out of
    ``request.state``; see ``get_auth_session_factory``.
    """

    request.state.session = session
    return session


def get_auth_session_factory(request: Request) -> Callable[[], Session]:
    """Return a factory for short-lived authentication lookup sessions.

    Authentication runs before the endpoint body, so it must not hold a pool
    connection for the whole request the way a request-scoped generator
    dependency does. ``get_current_user`` opens a session from this factory,
    reads the user and closes it again, which returns the connection to the
    pool before the endpoint's own session is opened.
    """

    return _application_session_factory(request)

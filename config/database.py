"""Synchronous SQLAlchemy database configuration."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

LOCAL_DATABASE_URL = "postgresql+psycopg://fastapi:fastapi@localhost:5432/fastapi_template"  # pragma: allowlist secret


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
        """Build settings from environment variables with local defaults."""

        return cls(
            url=os.getenv("DATABASE_URL", LOCAL_DATABASE_URL),
            pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
            pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
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


engine = build_engine(DatabaseSettings.from_env())
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Yield a request-scoped session and close it after use."""

    with SessionFactory() as session:
        yield session


def get_auth_session() -> Iterator[Session]:
    """Yield a separate request-scoped session for authentication lookup."""

    with SessionFactory() as session:
        yield session

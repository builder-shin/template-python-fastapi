"""Authentication configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import cast

from fastapi import Request

from config.settings import read_int, require_env


@dataclass(frozen=True, slots=True)
class AuthSettings:
    """Fail-closed JWT settings loaded from the process environment."""

    secret_key: str
    issuer: str = "template-python-fastapi"
    audience: str = "template-python-fastapi"
    access_expires_seconds: int = 900
    refresh_expires_seconds: int = 2_592_000
    leeway_seconds: int = 0

    @classmethod
    def from_env(cls) -> AuthSettings:
        secret_key = require_env("JWT_SECRET_KEY")
        if len(secret_key.encode("utf-8")) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 bytes")

        issuer = os.getenv("JWT_ISSUER", "template-python-fastapi")
        audience = os.getenv("JWT_AUDIENCE", "template-python-fastapi")
        if not issuer.strip():
            raise ValueError("JWT_ISSUER must not be blank")
        if not audience.strip():
            raise ValueError("JWT_AUDIENCE must not be blank")

        access_expires_seconds = read_int("JWT_ACCESS_EXPIRES_SECONDS", 900)
        refresh_expires_seconds = read_int("JWT_REFRESH_EXPIRES_SECONDS", 2_592_000)
        leeway_seconds = read_int("JWT_LEEWAY_SECONDS", 0)
        if access_expires_seconds <= 0:
            raise ValueError("JWT_ACCESS_EXPIRES_SECONDS must be greater than zero")
        if refresh_expires_seconds <= 0:
            raise ValueError("JWT_REFRESH_EXPIRES_SECONDS must be greater than zero")
        if leeway_seconds < 0:
            raise ValueError("JWT_LEEWAY_SECONDS must be non-negative")

        return cls(
            secret_key=secret_key,
            issuer=issuer,
            audience=audience,
            access_expires_seconds=access_expires_seconds,
            refresh_expires_seconds=refresh_expires_seconds,
            leeway_seconds=leeway_seconds,
        )


@dataclass(frozen=True, slots=True)
class RefreshSessionRetentionSettings:
    """Retention window for the expired refresh-session purge job.

    This deliberately lives outside :class:`AuthSettings` because the worker
    process runs the purge without any JWT environment variables; folding the
    window into ``AuthSettings.from_env`` would make the worker fail closed on a
    missing ``JWT_SECRET_KEY`` it never uses.
    """

    retention_seconds: int = 604_800

    @classmethod
    def from_env(cls) -> RefreshSessionRetentionSettings:
        retention_seconds = read_int("REFRESH_SESSION_RETENTION_SECONDS", 604_800)
        if retention_seconds < 0:
            raise ValueError("REFRESH_SESSION_RETENTION_SECONDS must be non-negative")

        return cls(retention_seconds=retention_seconds)


def get_auth_settings(request: Request) -> AuthSettings:
    """Return settings initialized by the application factory."""

    return cast(AuthSettings, request.app.state.auth_settings)

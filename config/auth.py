"""Authentication configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import cast

from fastapi import Request


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
        secret_key = os.getenv("JWT_SECRET_KEY")
        if secret_key is None:
            raise ValueError("JWT_SECRET_KEY is required")
        if len(secret_key.encode("utf-8")) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 bytes")

        issuer = os.getenv("JWT_ISSUER", "template-python-fastapi")
        audience = os.getenv("JWT_AUDIENCE", "template-python-fastapi")
        if not issuer.strip():
            raise ValueError("JWT_ISSUER must not be blank")
        if not audience.strip():
            raise ValueError("JWT_AUDIENCE must not be blank")

        access_expires_seconds = _read_integer("JWT_ACCESS_EXPIRES_SECONDS", 900)
        refresh_expires_seconds = _read_integer("JWT_REFRESH_EXPIRES_SECONDS", 2_592_000)
        leeway_seconds = _read_integer("JWT_LEEWAY_SECONDS", 0)
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


def _read_integer(variable: str, default: int) -> int:
    try:
        return int(os.getenv(variable, str(default)))
    except ValueError as error:
        raise ValueError(f"{variable} must be an integer") from error


def get_auth_settings(request: Request) -> AuthSettings:
    """Return settings initialized by the application factory."""

    return cast(AuthSettings, request.app.state.auth_settings)

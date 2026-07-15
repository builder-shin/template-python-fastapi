"""Fail-closed authentication settings tests."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI, Request

from config.auth import AuthSettings, get_auth_settings
from config.main import create_app

AUTH_ENVIRONMENT_VARIABLES = (
    "JWT_SECRET_KEY",
    "JWT_ISSUER",
    "JWT_AUDIENCE",
    "JWT_ACCESS_EXPIRES_SECONDS",
    "JWT_REFRESH_EXPIRES_SECONDS",
    "JWT_LEEWAY_SECONDS",
)


def _clear_auth_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in AUTH_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)


def test_auth_settings_require_an_explicit_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_auth_environment(monkeypatch)

    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        AuthSettings.from_env()


@pytest.mark.parametrize("secret", ["a" * 31, "비밀" * 5])
def test_auth_settings_reject_secrets_shorter_than_32_utf8_bytes(
    monkeypatch: pytest.MonkeyPatch,
    secret: str,
) -> None:
    _clear_auth_environment(monkeypatch)
    monkeypatch.setenv("JWT_SECRET_KEY", secret)

    with pytest.raises(ValueError, match="32 bytes"):
        AuthSettings.from_env()


def test_auth_settings_accept_a_32_byte_secret_and_use_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_auth_environment(monkeypatch)
    monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)

    settings = AuthSettings.from_env()

    assert settings == AuthSettings(
        secret_key="a" * 32,
        issuer="template-python-fastapi",
        audience="template-python-fastapi",
        access_expires_seconds=900,
        refresh_expires_seconds=2_592_000,
        leeway_seconds=0,
    )


def test_auth_settings_read_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_auth_environment(monkeypatch)
    monkeypatch.setenv("JWT_SECRET_KEY", "é" * 16)
    monkeypatch.setenv("JWT_ISSUER", "custom-issuer")
    monkeypatch.setenv("JWT_AUDIENCE", "custom-audience")
    monkeypatch.setenv("JWT_ACCESS_EXPIRES_SECONDS", "30")
    monkeypatch.setenv("JWT_REFRESH_EXPIRES_SECONDS", "60")
    monkeypatch.setenv("JWT_LEEWAY_SECONDS", "5")

    settings = AuthSettings.from_env()

    assert settings.secret_key == "é" * 16
    assert settings.issuer == "custom-issuer"
    assert settings.audience == "custom-audience"
    assert settings.access_expires_seconds == 30
    assert settings.refresh_expires_seconds == 60
    assert settings.leeway_seconds == 5


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("JWT_ISSUER", ""),
        ("JWT_ISSUER", "   "),
        ("JWT_AUDIENCE", ""),
        ("JWT_AUDIENCE", "   "),
        ("JWT_ACCESS_EXPIRES_SECONDS", "0"),
        ("JWT_ACCESS_EXPIRES_SECONDS", "-1"),
        ("JWT_REFRESH_EXPIRES_SECONDS", "0"),
        ("JWT_REFRESH_EXPIRES_SECONDS", "-1"),
        ("JWT_LEEWAY_SECONDS", "-1"),
    ],
)
def test_auth_settings_reject_invalid_overrides(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
) -> None:
    _clear_auth_environment(monkeypatch)
    monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError, match=variable):
        AuthSettings.from_env()


@pytest.mark.parametrize("secret", [None, "a" * 31])
def test_create_app_fails_closed_for_invalid_secret(
    monkeypatch: pytest.MonkeyPatch,
    secret: str | None,
) -> None:
    _clear_auth_environment(monkeypatch)
    if secret is not None:
        monkeypatch.setenv("JWT_SECRET_KEY", secret)

    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        create_app()


def test_create_app_stores_auth_settings_before_including_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_auth_environment(monkeypatch)
    monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
    observed_settings: list[AuthSettings] = []
    original_include_router: Callable[..., None] = FastAPI.include_router

    def include_router_with_observation(app: FastAPI, *args: object, **kwargs: object) -> None:
        observed_settings.append(app.state.auth_settings)
        original_include_router(app, *args, **kwargs)

    monkeypatch.setattr(FastAPI, "include_router", include_router_with_observation)

    app = create_app()

    assert observed_settings == [app.state.auth_settings]
    request = Request({"type": "http", "app": app})
    assert get_auth_settings(request) is app.state.auth_settings

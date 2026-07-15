"""Strict JSON:API input contracts for authentication endpoints."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.auth import LoginDocument, RefreshTokenDocument, RegisterDocument, normalize_email


def _register_document(**resource_overrides: object) -> dict[str, object]:
    resource: dict[str, object] = {
        "type": "users",
        "attributes": {"email": "person@example.com", "password": "a-secure-password"},  # pragma: allowlist secret
    }
    resource.update(resource_overrides)
    return {"data": resource}


def _login_document(**resource_overrides: object) -> dict[str, object]:
    resource: dict[str, object] = {
        "type": "authCredentials",
        "attributes": {"email": "person@example.com", "password": "a-secure-password"},  # pragma: allowlist secret
    }
    resource.update(resource_overrides)
    return {"data": resource}


def _refresh_document(**resource_overrides: object) -> dict[str, object]:
    resource: dict[str, object] = {
        "type": "refreshTokens",
        "attributes": {"refreshToken": "signed-refresh-token"},
    }
    resource.update(resource_overrides)
    return {"data": resource}


def test_register_document_accepts_only_strict_users_resource() -> None:
    document = RegisterDocument.model_validate(_register_document())

    assert document.data.type == "users"
    assert document.data.attributes.email == "person@example.com"
    assert document.data.attributes.password == "a-secure-password"  # pragma: allowlist secret


@pytest.mark.parametrize(
    "payload",
    [
        _register_document(type="authCredentials"),
        _register_document(id="f8dc4281-d022-4552-b1d4-ec6f46936d68"),
        _register_document(relationships={}),
        {**_register_document(), "meta": {}},
        _register_document(
            attributes={
                "email": "person@example.com",
                "password": "a-secure-password",  # pragma: allowlist secret
                "isActive": True,
            }
        ),
    ],
)
def test_register_document_rejects_wrong_type_and_extra_members(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RegisterDocument.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", "not-an-email"),
        ("email", f"{'a' * 244}@example.com"),
        ("email", 123),
        ("password", "short"),
        ("password", "x" * 129),
        ("password", 123456789012),
    ],
)
def test_register_document_rejects_invalid_email_and_password(field: str, value: object) -> None:
    attributes: dict[str, object] = {
        "email": "person@example.com",
        "password": "a-secure-password",  # pragma: allowlist secret
    }
    attributes[field] = value

    with pytest.raises(ValidationError):
        RegisterDocument.model_validate(_register_document(attributes=attributes))


def test_register_document_accepts_password_boundaries() -> None:
    for password in ("x" * 12, "x" * 128):
        document = RegisterDocument.model_validate(
            _register_document(attributes={"email": "person@example.com", "password": password})
        )
        assert document.data.attributes.password == password


def test_login_document_uses_auth_credentials_type_and_same_credentials_rules() -> None:
    document = LoginDocument.model_validate(_login_document())

    assert document.data.type == "authCredentials"
    assert document.data.attributes.email == "person@example.com"

    with pytest.raises(ValidationError):
        LoginDocument.model_validate(_login_document(type="users"))
    with pytest.raises(ValidationError):
        LoginDocument.model_validate(
            _login_document(attributes={"email": "not-an-email", "password": "x" * 12})  # pragma: allowlist secret
        )
    with pytest.raises(ValidationError):
        LoginDocument.model_validate(
            _login_document(attributes={"email": "person@example.com", "password": "short"})  # pragma: allowlist secret
        )


@pytest.mark.parametrize(
    "payload",
    [
        _refresh_document(type="authCredentials"),
        _refresh_document(id="f8dc4281-d022-4552-b1d4-ec6f46936d68"),
        _refresh_document(relationships={}),
        {**_refresh_document(), "meta": {}},
        _refresh_document(attributes={"refreshToken": "token", "accessToken": "secret"}),
        _refresh_document(attributes={"refreshToken": ""}),
        _refresh_document(attributes={"refreshToken": 123}),
    ],
)
def test_refresh_document_is_strict_and_rejects_extra_members(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RefreshTokenDocument.model_validate(payload)


def test_refresh_document_accepts_non_empty_refresh_token() -> None:
    document = RefreshTokenDocument.model_validate(_refresh_document())

    assert document.data.type == "refreshTokens"
    assert document.data.attributes.refresh_token == "signed-refresh-token"


def test_normalize_email_strips_surrounding_space_and_casefolds() -> None:
    assert normalize_email("  UsEr@Example.COM  ") == "user@example.com"

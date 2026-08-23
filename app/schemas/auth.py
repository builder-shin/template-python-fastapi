"""Strict JSON:API request documents for authentication endpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import EmailStr, Field

from app.jsonapi.naming import JsonApiWriteSchema

type AuthEmail = Annotated[EmailStr, Field(max_length=254)]
type AuthPassword = Annotated[str, Field(min_length=12, max_length=128)]
type RawRefreshToken = Annotated[str, Field(min_length=1)]


class CredentialsAttributes(JsonApiWriteSchema):
    """Email and password accepted by registration and login."""

    email: AuthEmail
    password: AuthPassword


class RegisterResource(JsonApiWriteSchema):
    """Registration resource with a fixed JSON:API type."""

    type: Literal["users"]
    attributes: CredentialsAttributes


class RegisterDocument(JsonApiWriteSchema):
    """Top-level registration document."""

    data: RegisterResource


class LoginResource(JsonApiWriteSchema):
    """Login resource with a fixed JSON:API type."""

    type: Literal["authCredentials"]
    attributes: CredentialsAttributes


class LoginDocument(JsonApiWriteSchema):
    """Top-level login document."""

    data: LoginResource


class RefreshTokenAttributes(JsonApiWriteSchema):
    """Raw refresh token accepted only in a JSON request body."""

    refresh_token: RawRefreshToken


class RefreshTokenResource(JsonApiWriteSchema):
    """Refresh or logout resource with a fixed JSON:API type."""

    type: Literal["refreshTokens"]
    attributes: RefreshTokenAttributes


class RefreshTokenDocument(JsonApiWriteSchema):
    """Top-level refresh and logout document."""

    data: RefreshTokenResource


def normalize_email(email: str) -> str:
    """Normalize a validated email immediately before persistence or lookup."""

    return email.strip().casefold()

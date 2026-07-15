"""Strict JSON:API request documents for authentication endpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


def _snake_to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(segment[:1].upper() + segment[1:] for segment in tail)


class _AuthSchema(BaseModel):
    model_config = ConfigDict(
        alias_generator=_snake_to_camel,
        extra="forbid",
        populate_by_name=True,
        strict=True,
    )


type AuthEmail = Annotated[EmailStr, Field(max_length=254)]
type AuthPassword = Annotated[str, Field(min_length=12, max_length=128)]
type RawRefreshToken = Annotated[str, Field(min_length=1)]


class CredentialsAttributes(_AuthSchema):
    """Email and password accepted by registration and login."""

    email: AuthEmail
    password: AuthPassword


class RegisterResource(_AuthSchema):
    """Registration resource with a fixed JSON:API type."""

    type: Literal["users"]
    attributes: CredentialsAttributes


class RegisterDocument(_AuthSchema):
    """Top-level registration document."""

    data: RegisterResource


class LoginResource(_AuthSchema):
    """Login resource with a fixed JSON:API type."""

    type: Literal["authCredentials"]
    attributes: CredentialsAttributes


class LoginDocument(_AuthSchema):
    """Top-level login document."""

    data: LoginResource


class RefreshTokenAttributes(_AuthSchema):
    """Raw refresh token accepted only in a JSON request body."""

    refresh_token: RawRefreshToken


class RefreshTokenResource(_AuthSchema):
    """Refresh or logout resource with a fixed JSON:API type."""

    type: Literal["refreshTokens"]
    attributes: RefreshTokenAttributes


class RefreshTokenDocument(_AuthSchema):
    """Top-level refresh and logout document."""

    data: RefreshTokenResource


def normalize_email(email: str) -> str:
    """Normalize a validated email immediately before persistence or lookup."""

    return email.strip().casefold()

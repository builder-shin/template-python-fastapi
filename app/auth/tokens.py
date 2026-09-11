"""Strict HS256 JWT and refresh-token hashing primitives."""

from __future__ import annotations

import hashlib
import hmac
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import jwt

from config.auth import AuthSettings

TokenType = Literal["access", "refresh"]
REQUIRED_CLAIMS = ["sub", "jti", "type", "iat", "exp", "iss", "aud"]


class InvalidToken(Exception):  # noqa: N818 - public taxonomy fixed by the auth contract
    """Raised when a token fails signature or strict claim validation."""


class TokenExpired(Exception):  # noqa: N818 - public taxonomy fixed by the auth contract
    """Raised when an otherwise valid token has expired."""


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Validated JWT claims with typed identifiers and timestamps."""

    sub: str
    jti: UUID
    type: TokenType
    iat: datetime
    exp: datetime
    iss: str
    aud: str


@dataclass(frozen=True, slots=True)
class AuthTokenResource:
    """Public access and refresh token pair returned after authentication."""

    id: UUID
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"]
    expires_in: int
    refresh_expires_in: int


def create_token(
    subject: str | UUID,
    *,
    token_type: TokenType,
    settings: AuthSettings,
    jti: UUID | None = None,
    now: datetime | None = None,
) -> str:
    """Create an HS256 access or refresh JWT."""

    issued_at = now or datetime.now(UTC)
    if issued_at.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    issued_at = issued_at.astimezone(UTC)
    lifetime = settings.access_expires_seconds if token_type == "access" else settings.refresh_expires_seconds
    payload = {
        "sub": str(subject),
        "jti": str(jti or uuid4()),
        "type": token_type,
        "iat": issued_at,
        "exp": issued_at + timedelta(seconds=lifetime),
        "iss": settings.issuer,
        "aud": settings.audience,
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_token(
    token: str,
    *,
    expected_type: TokenType,
    settings: AuthSettings,
) -> TokenClaims:
    """Decode a non-expired JWT using strict signature and claim validation."""

    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            audience=settings.audience,
            issuer=settings.issuer,
            leeway=settings.leeway_seconds,
            options={"require": REQUIRED_CLAIMS, "verify_exp": False, "verify_iat": False, "verify_nbf": False},
        )
    except jwt.ExpiredSignatureError as error:
        raise TokenExpired from error
    except jwt.InvalidTokenError as error:
        raise InvalidToken from error
    claims = _typed_claims(payload, expected_type=expected_type, settings=settings)
    _check_temporal(payload, settings=settings)
    return claims


def decode_expired_refresh_token(
    token: str,
    *,
    settings: AuthSettings,
) -> TokenClaims:
    """Decode a refresh JWT while deliberately bypassing expiration only."""

    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            audience=settings.audience,
            issuer=settings.issuer,
            leeway=settings.leeway_seconds,
            options={"require": REQUIRED_CLAIMS, "verify_exp": False, "verify_iat": False, "verify_nbf": False},
        )
    except jwt.InvalidTokenError as error:
        raise InvalidToken from error
    claims = _typed_claims(payload, expected_type="refresh", settings=settings)
    _check_temporal(payload, settings=settings, ignore_expiration=True)
    return claims


def hash_refresh_token(token: str) -> str:
    """Return the SHA-256 hexadecimal digest of a refresh token."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_token_matches(token: str, expected_hash: str) -> bool:
    """Compare a raw refresh token with a stored digest in constant time."""

    return hmac.compare_digest(hash_refresh_token(token), expected_hash)


def _typed_claims(
    payload: dict[str, Any],
    *,
    expected_type: TokenType,
    settings: AuthSettings,
) -> TokenClaims:
    # Signature, required claims, issuer and audience are checked by PyJWT.
    # Validate shape/ranges before any temporal classification so malformed
    # expired payloads remain INVALID_TOKEN in every implementation.
    try:
        sub = payload["sub"]
        raw_jti = payload["jti"]
        raw_type = payload["type"]
        raw_iat = payload["iat"]
        raw_exp = payload["exp"]
        issuer = payload["iss"]
        audience = payload["aud"]
        if not isinstance(sub, str) or not sub:
            raise ValueError
        if not isinstance(raw_jti, str):
            raise ValueError
        jti = UUID(raw_jti)
        if raw_type != expected_type:
            raise ValueError
        if not _is_numeric_date(raw_iat) or not _is_numeric_date(raw_exp):
            raise ValueError
        if issuer != settings.issuer:
            raise ValueError
        if audience != settings.audience:
            raise ValueError
        raw_nbf = payload.get("nbf")
        if "nbf" in payload and not _is_numeric_date(raw_nbf):
            raise ValueError
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        iat = epoch + timedelta(seconds=raw_iat)
        exp = epoch + timedelta(seconds=raw_exp)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise InvalidToken from error

    return TokenClaims(
        sub=sub,
        jti=jti,
        type=expected_type,
        iat=iat,
        exp=exp,
        iss=issuer,
        aud=audience,
    )


def _is_numeric_date(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and -62135596800 <= value < 253402300800
    )


def _check_temporal(payload: dict[str, Any], *, settings: AuthSettings, ignore_expiration: bool = False) -> None:
    now = datetime.now(UTC).timestamp()
    if int(payload["iat"]) > now + settings.leeway_seconds:
        raise InvalidToken
    if "nbf" in payload and int(payload["nbf"]) > now + settings.leeway_seconds:
        raise InvalidToken
    if not ignore_expiration and int(payload["exp"]) <= now - settings.leeway_seconds:
        raise TokenExpired

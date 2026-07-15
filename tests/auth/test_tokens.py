"""Strict JWT and refresh-token hash primitive tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
import pytest

from app.auth.tokens import (
    InvalidToken,
    TokenExpired,
    create_token,
    decode_expired_refresh_token,
    decode_token,
    hash_refresh_token,
    refresh_token_matches,
)
from config.auth import AuthSettings

SECRET = "s" * 64  # pragma: allowlist secret
OTHER_SECRET = "o" * 64  # pragma: allowlist secret
FIXED_NOW = datetime(2026, 7, 15, 3, 0, tzinfo=UTC)
FIXED_JTI = UUID("31ef8582-5418-4e62-891c-cfc22d356e5a")


@pytest.fixture
def settings() -> AuthSettings:
    return AuthSettings(secret_key=SECRET)


def _valid_payload(settings: AuthSettings, *, token_type: str = "access") -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "sub": "user-123",
        "jti": str(FIXED_JTI),
        "type": token_type,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "iss": settings.issuer,
        "aud": settings.audience,
    }


def _encode(payload: dict[str, Any], *, secret: str = SECRET, algorithm: str = "HS256") -> str:
    return jwt.encode(payload, secret, algorithm=algorithm)


@pytest.mark.parametrize(
    ("token_type", "expected_lifetime"),
    [("access", 900), ("refresh", 2_592_000)],
)
def test_create_token_emits_all_strict_claims_with_the_configured_lifetime(
    settings: AuthSettings,
    token_type: str,
    expected_lifetime: int,
) -> None:
    token = create_token(
        "user-123",
        token_type=token_type,
        settings=settings,
        jti=FIXED_JTI,
        now=FIXED_NOW,
    )

    payload = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        options={"verify_aud": False, "verify_exp": False, "verify_iat": False},
    )

    assert payload == {
        "sub": "user-123",
        "jti": str(FIXED_JTI),
        "type": token_type,
        "iat": int(FIXED_NOW.timestamp()),
        "exp": int(FIXED_NOW.timestamp()) + expected_lifetime,
        "iss": settings.issuer,
        "aud": settings.audience,
    }


def test_create_token_generates_a_uuid_jti_by_default(settings: AuthSettings) -> None:
    token = create_token("user-123", token_type="access", settings=settings)
    payload = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        audience=settings.audience,
        issuer=settings.issuer,
    )

    assert str(UUID(payload["jti"])) == payload["jti"]


def test_decode_token_returns_typed_timezone_aware_claims(settings: AuthSettings) -> None:
    token = create_token("user-123", token_type="access", settings=settings, jti=FIXED_JTI)

    claims = decode_token(token, expected_type="access", settings=settings)

    assert claims.sub == "user-123"
    assert claims.jti == FIXED_JTI
    assert claims.type == "access"
    assert claims.iat.tzinfo is UTC
    assert claims.exp.tzinfo is UTC
    assert claims.iss == settings.issuer
    assert claims.aud == settings.audience


@pytest.mark.parametrize(
    ("token", "expected_exception"),
    [
        ("not-a-token", InvalidToken),
        (_encode({"some": "payload"}, secret=OTHER_SECRET), InvalidToken),
    ],
)
def test_decode_token_maps_malformed_or_bad_signature_to_invalid_token(
    settings: AuthSettings,
    token: str,
    expected_exception: type[Exception],
) -> None:
    with pytest.raises(expected_exception):
        decode_token(token, expected_type="access", settings=settings)


def test_decode_token_rejects_algorithms_other_than_hs256(settings: AuthSettings) -> None:
    token = _encode(_valid_payload(settings), algorithm="HS384")

    with pytest.raises(InvalidToken):
        decode_token(token, expected_type="access", settings=settings)


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("iss", "wrong-issuer"),
        ("aud", "wrong-audience"),
        ("type", "refresh"),
        ("jti", "not-a-uuid"),
    ],
)
def test_decode_token_rejects_invalid_strict_claims(
    settings: AuthSettings,
    claim: str,
    value: str,
) -> None:
    payload = _valid_payload(settings)
    payload[claim] = value

    with pytest.raises(InvalidToken):
        decode_token(_encode(payload), expected_type="access", settings=settings)


@pytest.mark.parametrize("missing_claim", ["sub", "jti", "type", "iat", "exp", "iss", "aud"])
def test_decode_token_rejects_each_missing_required_claim(
    settings: AuthSettings,
    missing_claim: str,
) -> None:
    payload = _valid_payload(settings)
    del payload[missing_claim]

    with pytest.raises(InvalidToken):
        decode_token(_encode(payload), expected_type="access", settings=settings)


def test_decode_token_rejects_a_future_issued_at(settings: AuthSettings) -> None:
    payload = _valid_payload(settings)
    payload["iat"] = datetime.now(UTC) + timedelta(minutes=1)

    with pytest.raises(InvalidToken):
        decode_token(_encode(payload), expected_type="access", settings=settings)


def test_decode_token_maps_expiry_at_the_current_second_to_token_expired(
    settings: AuthSettings,
) -> None:
    payload = _valid_payload(settings)
    payload["exp"] = int(datetime.now(UTC).timestamp())

    with pytest.raises(TokenExpired):
        decode_token(_encode(payload), expected_type="access", settings=settings)


def test_expired_refresh_decoder_only_bypasses_expiration(settings: AuthSettings) -> None:
    payload = _valid_payload(settings, token_type="refresh")
    payload["exp"] = datetime.now(UTC) - timedelta(seconds=1)
    token = _encode(payload)

    claims = decode_expired_refresh_token(token, settings=settings)

    assert claims.jti == FIXED_JTI
    assert claims.type == "refresh"
    assert claims.exp < datetime.now(UTC)


@pytest.mark.parametrize(
    ("mutation", "secret"),
    [
        ({"type": "access"}, SECRET),
        ({"iss": "wrong-issuer"}, SECRET),
        ({"aud": "wrong-audience"}, SECRET),
        ({}, OTHER_SECRET),
    ],
)
def test_expired_refresh_decoder_preserves_other_validation(
    settings: AuthSettings,
    mutation: dict[str, str],
    secret: str,
) -> None:
    payload = _valid_payload(settings, token_type="refresh")
    payload["exp"] = datetime.now(UTC) - timedelta(seconds=1)
    payload.update(mutation)

    with pytest.raises(InvalidToken):
        decode_expired_refresh_token(_encode(payload, secret=secret), settings=settings)


def test_expired_refresh_decoder_requires_all_claims(settings: AuthSettings) -> None:
    payload = _valid_payload(settings, token_type="refresh")
    payload["exp"] = datetime.now(UTC) - timedelta(seconds=1)
    del payload["sub"]

    with pytest.raises(InvalidToken):
        decode_expired_refresh_token(_encode(payload), settings=settings)


def test_refresh_token_hash_is_sha256_hex() -> None:
    assert hash_refresh_token("refresh-token") == (
        "0eb17643d4e9261163783a420859c92c7d212fa9624106a12b510afbec266120"  # pragma: allowlist secret
    )


def test_refresh_token_matching_uses_constant_time_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.auth import tokens

    expected_hash = hash_refresh_token("refresh-token")
    comparisons: list[tuple[str, str]] = []

    def compare_digest(left: str, right: str) -> bool:
        comparisons.append((left, right))
        return left == right

    monkeypatch.setattr(tokens.hmac, "compare_digest", compare_digest)

    assert refresh_token_matches("refresh-token", expected_hash) is True
    assert refresh_token_matches("wrong-token", expected_hash) is False
    assert comparisons == [
        (hash_refresh_token("refresh-token"), expected_hash),
        (hash_refresh_token("wrong-token"), expected_hash),
    ]

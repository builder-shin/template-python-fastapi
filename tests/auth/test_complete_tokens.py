"""JWT validity must be platform independent and precede expiration classification."""

from datetime import UTC, datetime
from uuid import UUID

import jwt
import pytest

from app.auth.tokens import InvalidToken, decode_token
from config.auth import AuthSettings

SETTINGS = AuthSettings(secret_key="s" * 64)
IDENTIFIER = "12345678-1234-5678-9012-123456789012"


def signed(changes: dict[str, object]) -> str:
    now = int(datetime.now(UTC).timestamp())
    return jwt.encode(
        {
            "sub": IDENTIFIER,
            "jti": IDENTIFIER,
            "iat": now,
            "exp": now + 3600,
            "type": "refresh",
            "iss": SETTINGS.issuer,
            "aud": SETTINGS.audience,
        }
        | changes,
        SETTINGS.secret_key,
        algorithm="HS256",
    )


@pytest.mark.parametrize("iat", [-62135596800, -1, 0])
def test_historical_iat_is_platform_independent(iat: int) -> None:
    assert decode_token(signed({"iat": iat}), expected_type="refresh", settings=SETTINGS).jti == UUID(IDENTIFIER)


@pytest.mark.parametrize("changes", [{"nbf": True}, {"nbf": "1"}, {"exp": 0, "jti": "bad"}])
def test_invalid_claims_before_expiry(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidToken):
        decode_token(signed(changes), expected_type="refresh", settings=SETTINGS)

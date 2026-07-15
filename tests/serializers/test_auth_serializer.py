"""Public JSON:API serializer contracts for users and issued token pairs."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.auth.tokens import AuthTokenResource
from app.models import User
from app.serializers import AuthTokenSerializer, UserSerializer


def test_user_serializer_exposes_public_profile_at_stable_me_location() -> None:
    user = User(
        id=UUID("8e4835e1-5e3b-49eb-8d7a-cb3567fd39b8"),
        email="person@example.com",
        password_hash="$argon2id$internal-secret",
        is_active=True,
        created_at=datetime(2026, 7, 15, 1, 2, 3, tzinfo=UTC),
        updated_at=datetime(2026, 7, 15, 4, 5, 6, tzinfo=UTC),
    )

    resource = UserSerializer.serialize(user)

    assert resource.model_dump(mode="json", exclude_none=True) == {
        "type": "users",
        "id": "8e4835e1-5e3b-49eb-8d7a-cb3567fd39b8",
        "attributes": {
            "email": "person@example.com",
            "isActive": True,
            "createdAt": "2026-07-15T01:02:03+00:00",
            "updatedAt": "2026-07-15T04:05:06+00:00",
        },
        "links": {"self": "/api/v1/users/me"},
    }
    assert "password" not in resource.attributes
    assert "passwordHash" not in resource.attributes


def test_auth_token_serializer_exposes_only_token_pair_contract_without_links() -> None:
    token_pair = AuthTokenResource(
        id=UUID("fb26a088-6810-4257-899f-6b8a40f4125e"),
        access_token="access-secret",
        refresh_token="refresh-secret",
        token_type="Bearer",
        expires_in=900,
        refresh_expires_in=2_592_000,
    )

    resource = AuthTokenSerializer.serialize(token_pair)

    assert resource.model_dump(mode="json", exclude_none=True) == {
        "type": "authTokens",
        "id": "fb26a088-6810-4257-899f-6b8a40f4125e",
        "attributes": {
            "accessToken": "access-secret",
            "refreshToken": "refresh-secret",
            "tokenType": "Bearer",
            "expiresIn": 900,
            "refreshExpiresIn": 2_592_000,
        },
    }
    assert "links" not in resource.model_fields_set

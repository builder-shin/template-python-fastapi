"""Argon2 password hashing primitives."""

from __future__ import annotations

from pwdlib import PasswordHash

PASSWORD_HASH = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = PASSWORD_HASH.hash("dummy-password-value-not-used-for-login")


def hash_password(password: str) -> str:
    """Hash a password with the recommended Argon2 configuration."""

    return PASSWORD_HASH.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against an Argon2 hash."""

    return PASSWORD_HASH.verify(password, password_hash)

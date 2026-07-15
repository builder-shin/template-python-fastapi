"""Argon2 password primitive tests."""

from __future__ import annotations

from app.auth.passwords import DUMMY_PASSWORD_HASH, hash_password, verify_password


def test_hash_password_uses_argon2() -> None:
    password_hash = hash_password("correct horse battery staple")

    assert password_hash.startswith("$argon2")


def test_verify_password_accepts_the_correct_password() -> None:
    password_hash = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", password_hash) is True


def test_verify_password_rejects_an_incorrect_password() -> None:
    password_hash = hash_password("correct horse battery staple")

    assert verify_password("incorrect password", password_hash) is False


def test_hash_password_uses_a_fresh_salt() -> None:
    first_hash = hash_password("correct horse battery staple")
    second_hash = hash_password("correct horse battery staple")

    assert first_hash != second_hash


def test_dummy_hash_uses_the_same_argon2_verification_path() -> None:
    assert DUMMY_PASSWORD_HASH.startswith("$argon2")
    assert verify_password("an unknown user's password", DUMMY_PASSWORD_HASH) is False

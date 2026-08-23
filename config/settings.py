"""Shared environment loading rules for every configuration module."""

from __future__ import annotations

import os

__all__ = ["read_int", "require_env"]


def require_env(variable: str) -> str:
    """Return a required environment variable or fail closed with its name."""

    value = os.getenv(variable)
    if value is None or not value.strip():
        raise ValueError(f"{variable} is required")
    return value


def read_int(variable: str, default: int) -> int:
    """Return an integer environment variable, naming the variable on failure."""

    try:
        return int(os.getenv(variable, str(default)))
    except ValueError as error:
        raise ValueError(f"{variable} must be an integer") from error

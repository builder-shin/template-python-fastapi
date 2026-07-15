"""Validated API input schemas."""

from app.schemas.auth import LoginDocument, RefreshTokenDocument, RegisterDocument, normalize_email
from app.schemas.example import (
    EXAMPLE_QUERY_POLICY,
    ExampleCreate,
    ExampleRelationships,
    ExampleReplace,
    ExampleUpdate,
)

__all__ = [
    "EXAMPLE_QUERY_POLICY",
    "ExampleCreate",
    "ExampleRelationships",
    "ExampleReplace",
    "ExampleUpdate",
    "LoginDocument",
    "RefreshTokenDocument",
    "RegisterDocument",
    "normalize_email",
]

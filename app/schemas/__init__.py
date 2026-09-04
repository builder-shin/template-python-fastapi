"""Validated API input schemas."""

from app.schemas.auth import LoginDocument, RefreshTokenDocument, RegisterDocument, normalize_email
from app.schemas.example import (
    EXAMPLE_QUERY_POLICY,
    ExampleCreate,
    ExampleRelationships,
    ExampleReplace,
    ExampleUpdate,
)
from app.schemas.example_category import EXAMPLE_CATEGORY_QUERY_POLICY
from app.schemas.example_tag import EXAMPLE_TAG_QUERY_POLICY

__all__ = [
    "EXAMPLE_CATEGORY_QUERY_POLICY",
    "EXAMPLE_QUERY_POLICY",
    "EXAMPLE_TAG_QUERY_POLICY",
    "ExampleCreate",
    "ExampleRelationships",
    "ExampleReplace",
    "ExampleUpdate",
    "LoginDocument",
    "RefreshTokenDocument",
    "RegisterDocument",
    "normalize_email",
]

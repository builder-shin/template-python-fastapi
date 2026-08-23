"""Single source of the public JSON:API name rule and the strict write-input base."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


def snake_to_camel(value: str) -> str:
    """Convert an internal snake_case name to its public camelCase JSON:API name.

    Request schemas and response serializers must derive public attribute and
    relationship names from this one function; a second copy would let the two
    sides of the wire contract drift apart silently.
    """

    head, *tail = value.split("_")
    return head + "".join(segment[:1].upper() + segment[1:] for segment in tail)


WRITE_MODEL_CONFIG = ConfigDict(
    alias_generator=snake_to_camel,
    extra="forbid",
    populate_by_name=True,
    strict=True,
)
"""Strict camelCase configuration shared by every JSON:API request model.

``create_model`` call sites need the raw ``ConfigDict``; declarative schemas
inherit the same configuration through :class:`JsonApiWriteSchema`.
"""


class JsonApiWriteSchema(BaseModel):
    """Base class for strict camelCase JSON:API request models."""

    model_config = WRITE_MODEL_CONFIG

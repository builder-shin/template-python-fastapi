"""Type-safe JSON:API 1.1 document models."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)
from pydantic.experimental.missing_sentinel import MISSING

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

type Meta = dict[str, JsonValue]


def _is_missing(value: object) -> bool:
    return value is MISSING


def _serialization_selects_data(info: SerializationInfo) -> bool:
    if info.include is not None and "data" not in info.include:
        return False

    if info.exclude is None or "data" not in info.exclude:
        return True
    if isinstance(info.exclude, Mapping):
        directive: Any = info.exclude["data"]
        return directive is not True and directive is not Ellipsis
    return False


class _JsonApiModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        allow_inf_nan=False,
    )


class JsonApiVersion(_JsonApiModel):
    version: Literal["1.1"] = "1.1"


class LinkObject(_JsonApiModel):
    href: str
    rel: str = MISSING  # type: ignore[assignment]
    describedby: str = MISSING  # type: ignore[assignment]
    title: str = MISSING  # type: ignore[assignment]
    type: str = MISSING  # type: ignore[assignment]
    hreflang: str | list[str] = MISSING  # type: ignore[assignment]
    meta: Meta = MISSING  # type: ignore[assignment]


type Link = str | LinkObject | None
type Links = dict[str, Link]


class ResourceIdentifier(_JsonApiModel):
    type: str
    id: str
    meta: Meta = MISSING  # type: ignore[assignment]


class RelationshipObject(_JsonApiModel):
    data: ResourceIdentifier | list[ResourceIdentifier] | None = None
    links: Links = MISSING  # type: ignore[assignment]
    meta: Meta = MISSING  # type: ignore[assignment]

    @model_validator(mode="after")
    def require_data_links_or_meta(self) -> Self:
        has_data = "data" in self.model_fields_set
        has_links = "links" in self.model_fields_set and not _is_missing(self.links)
        has_meta = "meta" in self.model_fields_set and not _is_missing(self.meta)
        if not (has_data or has_links or has_meta):
            raise ValueError("a relationship requires data, links, or meta")
        return self

    @model_serializer(mode="wrap")
    def serialize_explicit_null_data(  # type: ignore[no-untyped-def]
        self,
        handler: SerializerFunctionWrapHandler,
        info: SerializationInfo,
    ):
        serialized = handler(self)
        if isinstance(serialized, dict):
            if "data" not in self.model_fields_set:
                serialized.pop("data", None)
            elif self.data is None and _serialization_selects_data(info):
                serialized.setdefault("data", None)
        return serialized


class ResourceObject(ResourceIdentifier):
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    relationships: dict[str, RelationshipObject] = MISSING  # type: ignore[assignment]
    links: Links = MISSING  # type: ignore[assignment]


type ResourceData = ResourceObject | list[ResourceObject] | None
type RelationshipData = ResourceIdentifier | list[ResourceIdentifier] | None


class ErrorSource(_JsonApiModel):
    pointer: str = MISSING  # type: ignore[assignment]
    parameter: str = MISSING  # type: ignore[assignment]
    header: str = MISSING  # type: ignore[assignment]


class ErrorObject(_JsonApiModel):
    id: str = MISSING  # type: ignore[assignment]
    status: str = MISSING  # type: ignore[assignment]
    code: str = MISSING  # type: ignore[assignment]
    title: str = MISSING  # type: ignore[assignment]
    detail: str = MISSING  # type: ignore[assignment]
    source: ErrorSource = MISSING  # type: ignore[assignment]
    links: Links = MISSING  # type: ignore[assignment]
    meta: Meta = MISSING  # type: ignore[assignment]

    @model_validator(mode="after")
    def require_at_least_one_member(self) -> Self:
        member_names = {"id", "links", "status", "code", "title", "detail", "source", "meta"}
        present_members = self.model_fields_set.intersection(member_names)
        if not any(not _is_missing(getattr(self, member_name)) for member_name in present_members):
            raise ValueError("an error object requires at least one member")
        return self


class JsonApiDocument(_JsonApiModel):
    data: ResourceData = None
    errors: list[ErrorObject] = MISSING  # type: ignore[assignment]
    meta: Meta = MISSING  # type: ignore[assignment]
    jsonapi: JsonApiVersion = Field(default_factory=JsonApiVersion)
    links: Links = MISSING  # type: ignore[assignment]
    included: list[ResourceObject] = MISSING  # type: ignore[assignment]

    @model_validator(mode="after")
    def validate_top_level_members(self) -> Self:
        has_data = "data" in self.model_fields_set
        has_errors = "errors" in self.model_fields_set and not _is_missing(self.errors)
        has_meta = "meta" in self.model_fields_set and not _is_missing(self.meta)
        has_included = "included" in self.model_fields_set and not _is_missing(self.included)

        if has_data and has_errors:
            raise ValueError("data and errors cannot appear in the same JSON:API document")
        if not (has_data or has_errors or has_meta):
            raise ValueError("a JSON:API document requires data, errors, or meta")
        if has_included and not has_data:
            raise ValueError("included requires data in a JSON:API document")
        return self

    @model_serializer(mode="wrap")
    def serialize_explicit_null_data(  # type: ignore[no-untyped-def]
        self,
        handler: SerializerFunctionWrapHandler,
        info: SerializationInfo,
    ):
        serialized = handler(self)
        if isinstance(serialized, dict):
            if "data" not in self.model_fields_set:
                serialized.pop("data", None)
            elif self.data is None and _serialization_selects_data(info):
                serialized.setdefault("data", None)
        return serialized


class SuccessDocument(JsonApiDocument):
    data: ResourceData


class ErrorDocument(JsonApiDocument):
    errors: list[ErrorObject] = Field(min_length=1)


class RelationshipDocument(_JsonApiModel):
    """Top-level relationship linkage document returned by relationship URLs."""

    data: RelationshipData
    meta: Meta = MISSING  # type: ignore[assignment]
    jsonapi: JsonApiVersion = Field(default_factory=JsonApiVersion)
    links: Links = MISSING  # type: ignore[assignment]

    @model_serializer(mode="wrap")
    def serialize_explicit_null_data(  # type: ignore[no-untyped-def]
        self,
        handler: SerializerFunctionWrapHandler,
        info: SerializationInfo,
    ):
        serialized = handler(self)
        if isinstance(serialized, dict) and self.data is None and _serialization_selects_data(info):
            serialized.setdefault("data", None)
        return serialized

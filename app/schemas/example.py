"""Example JSON:API write schemas and allowlisted query policy."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator
from pydantic.experimental.missing_sentinel import MISSING

from app.jsonapi.documents import ResourceIdentifier
from app.jsonapi.query import FilterField, QueryPolicy, SortTerm
from app.models import Example, ExampleStatus


def _snake_to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(segment[:1].upper() + segment[1:] for segment in tail)


class _WriteSchema(BaseModel):
    model_config = ConfigDict(
        alias_generator=_snake_to_camel,
        extra="forbid",
        populate_by_name=True,
    )


type Score = Annotated[int, Field(strict=True, ge=0, le=100)]
type Title = Annotated[StrictStr, Field(min_length=1, max_length=200)]


class ExampleCreate(_WriteSchema):
    """Attributes accepted when creating an example."""

    title: Title
    description: StrictStr | None = None
    status: ExampleStatus
    score: Score


class ExampleUpdate(_WriteSchema):
    """Sparse attributes accepted by PATCH."""

    title: Title = MISSING  # type: ignore[assignment]
    description: StrictStr | None = MISSING  # type: ignore[assignment]
    status: ExampleStatus = MISSING  # type: ignore[assignment]
    score: Score = MISSING  # type: ignore[assignment]


class ExampleReplace(_WriteSchema):
    """Complete attributes accepted by PUT replacement/upsert."""

    title: Title
    description: StrictStr | None = None
    status: ExampleStatus
    score: Score


class ToOneRelationship(_WriteSchema):
    """JSON:API to-one relationship linkage input."""

    data: ResourceIdentifier | None


class ToManyRelationship(_WriteSchema):
    """JSON:API to-many relationship linkage input."""

    data: list[ResourceIdentifier]


class ExampleRelationships(_WriteSchema):
    """Optional example relationship members used by relationship-aware actions."""

    category: ToOneRelationship = MISSING  # type: ignore[assignment]
    tags: ToManyRelationship = MISSING  # type: ignore[assignment]

    @model_validator(mode="after")
    def require_at_least_one_relationship(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one example relationship is required")
        return self


def _parse_status(value: str) -> ExampleStatus:
    return ExampleStatus(value)


def _parse_uuid(value: str) -> UUID:
    return UUID(value)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("datetime filter must include a UTC offset")
    return parsed


EXAMPLE_QUERY_POLICY = QueryPolicy(
    filters={
        "title": FilterField(
            column=Example.title,
            parser=str,
            operators=frozenset({"exact", "contains"}),
        ),
        "status": FilterField(
            column=Example.status,
            parser=_parse_status,
            operators=frozenset({"exact", "in"}),
        ),
        "score": FilterField(
            column=Example.score,
            parser=int,
            operators=frozenset({"exact", "gt", "gte", "lt", "lte", "in"}),
        ),
        "category.id": FilterField(
            column=Example.category_id,
            parser=_parse_uuid,
            operators=frozenset({"exact", "in", "isNull"}),
        ),
        "createdAt": FilterField(
            column=Example.created_at,
            parser=_parse_datetime,
            operators=frozenset({"exact", "gt", "gte", "lt", "lte"}),
        ),
    },
    sorts={
        "title": Example.title,
        "status": Example.status,
        "score": Example.score,
        "createdAt": Example.created_at,
        "updatedAt": Example.updated_at,
    },
    includes=frozenset({"category", "tags"}),
    default_sort=(SortTerm("createdAt", descending=True),),
    tie_breaker=SortTerm("id", column=Example.id),
)

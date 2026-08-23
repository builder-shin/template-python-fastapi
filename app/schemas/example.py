"""Example JSON:API write schemas and allowlisted query policy."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, model_validator
from pydantic.experimental.missing_sentinel import MISSING

from app.jsonapi.documents import ResourceIdentifier
from app.jsonapi.naming import JsonApiWriteSchema
from app.jsonapi.query import FilterField, QueryPolicy, SortTerm
from app.models import Example, ExampleStatus

type Score = Annotated[int, Field(ge=0, le=100)]
type Title = Annotated[str, Field(min_length=1, max_length=200)]
# ``status`` is annotated inline on every schema rather than through a ``type``
# alias: a PEP 695 alias becomes the published OpenAPI component name, so an
# alias here would rename ``ExampleStatus`` in ``components.schemas``.
# ``Field(strict=False)`` is needed because FastAPI validates request bodies with
# ``validate_python``, where the base ``strict=True`` would reject the JSON string
# form of this ``StrEnum``; opting this one field out keeps ``"active"`` valid
# while still rejecting non-string input such as ``0``.


class ExampleCreate(JsonApiWriteSchema):
    """Attributes accepted when creating an example."""

    title: Title
    description: str | None = None
    status: Annotated[ExampleStatus, Field(strict=False)]
    score: Score


class ExampleUpdate(JsonApiWriteSchema):
    """Sparse attributes accepted by PATCH."""

    title: Title = MISSING  # type: ignore[assignment]
    description: str | None = MISSING  # type: ignore[assignment]
    status: Annotated[ExampleStatus, Field(strict=False)] = MISSING  # type: ignore[assignment]
    score: Score = MISSING  # type: ignore[assignment]


class ExampleReplace(JsonApiWriteSchema):
    """Complete attributes accepted by PUT replacement/upsert."""

    title: Title
    description: str | None = None
    status: Annotated[ExampleStatus, Field(strict=False)]
    score: Score


class ToOneRelationship(JsonApiWriteSchema):
    """JSON:API to-one relationship linkage input."""

    data: ResourceIdentifier | None


class ToManyRelationship(JsonApiWriteSchema):
    """JSON:API to-many relationship linkage input."""

    data: list[ResourceIdentifier]


class ExampleRelationships(JsonApiWriteSchema):
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


# Index coverage for this policy (see the root AGENTS.md rule on keeping a
# QueryPolicy and the physical schema in sync):
#   - default sort ``createdAt DESC`` plus the ``id`` tie breaker is covered by
#     ``ix_examples_created_at_id`` (created_at DESC, id).
#   - ``category.id`` is covered by ``ix_examples_category_id``.
#   - sorting on title/status/score/updatedAt and filtering on
#     status/score/createdAt are deliberately left unindexed: ``examples`` is a
#     template demonstration resource with no real access pattern to justify the
#     write cost. A real service must re-decide this, and note that every sort
#     gets ``id ASC`` appended, so a useful index is ``(<column>, id)``, not the
#     bare column.
#   - ``title`` ``contains`` compiles to ``LIKE '%...%'`` (app/jsonapi/query.py),
#     which no btree can serve. Adopting pg_trgm is deferred until a real usage
#     pattern justifies the extension; the operator stays in the public
#     allowlist meanwhile.
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

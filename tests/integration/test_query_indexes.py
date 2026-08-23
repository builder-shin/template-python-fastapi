"""Regressions coupling EXAMPLE_QUERY_POLICY ordering to its physical index."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Select, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from starlette.datastructures import QueryParams

from app.jsonapi.query import (
    QuerySpec,
    apply_keyset,
    apply_pagination,
    apply_sort,
    encode_cursor,
    keyset_sorts,
    parse_query,
)
from app.models import Example, ExampleStatus
from app.schemas.example import EXAMPLE_QUERY_POLICY


def _default_list_plan(db_session: Session) -> list[str]:
    spec = parse_query(QueryParams(""), EXAMPLE_QUERY_POLICY)
    statement = apply_pagination(
        apply_sort(select(Example), spec.sorts, EXAMPLE_QUERY_POLICY),
        spec.page,
    )
    compiled = statement.compile(
        dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]  # SQLAlchemy dialect factory is untyped.
        compile_kwargs={"literal_binds": True},
    )
    # An empty table makes a sequential scan trivially cheapest, so force the
    # planner to reveal whether an ordering index exists at all.
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    return [row[0] for row in db_session.execute(text(f"EXPLAIN {compiled}"))]


def test_default_example_list_sort_uses_the_created_at_index(db_session: Session) -> None:
    plan = _default_list_plan(db_session)

    assert any("Index Scan using ix_examples_created_at_id on examples" in line for line in plan), plan
    assert not any("Sort" in line for line in plan), plan


def test_examples_created_at_index_definition_keeps_sort_direction(db_session: Session) -> None:
    definition = db_session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE tablename = 'examples' AND indexname = :name"),
        {"name": "ix_examples_created_at_id"},
    ).scalar_one()

    assert definition.endswith("btree (created_at DESC, id)"), definition


def _seed_tied_examples(session: Session, *, rows: int, tie_groups: int) -> None:
    """Seed rows whose leading sort value repeats, so ties exercise the AND prefixes."""

    base = datetime(2026, 1, 1, tzinfo=UTC)
    session.add_all(
        [
            Example(
                title=f"keyset-{index:04d}",
                description=None,
                status=ExampleStatus.ACTIVE,
                score=index % 100,
                created_at=base + timedelta(seconds=index % tie_groups),
            )
            for index in range(rows)
        ]
    )
    session.flush()
    session.execute(text("ANALYZE examples"))


def _cursor_page_statement(query: str) -> tuple[QuerySpec, Select[Any]]:
    spec = parse_query(QueryParams(query), EXAMPLE_QUERY_POLICY)
    cursor = spec.page.cursor
    assert cursor is not None
    statement = apply_keyset(select(Example), spec.sorts, EXAMPLE_QUERY_POLICY, cursor)
    statement = apply_sort(statement, keyset_sorts(spec.sorts, cursor), EXAMPLE_QUERY_POLICY)
    return spec, apply_pagination(statement, spec.page)


def _explain(db_session: Session, statement: Select[Any]) -> list[str]:
    compiled = statement.compile(
        dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]  # SQLAlchemy dialect factory is untyped.
        compile_kwargs={"literal_binds": True},
    )
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    return [row[0] for row in db_session.execute(text(f"EXPLAIN {compiled}"))]


@pytest.mark.parametrize("parameter", ["page[after]", "page[before]"])
def test_positioned_cursor_page_starts_the_index_scan_instead_of_filtering(
    db_session: Session,
    parameter: str,
) -> None:
    """The keyset predicate must be sargable, not a bare ``OR`` the planner can only filter.

    Without a leading bound conjoined to the disjunction PostgreSQL reads the ordering
    index from its start and drops every row before the cursor with ``Rows Removed by
    Filter`` — byte for byte the deep-OFFSET cost the cursor mode exists to avoid.
    """

    _seed_tied_examples(db_session, rows=400, tie_groups=20)
    ordered = apply_sort(select(Example), (), EXAMPLE_QUERY_POLICY)
    positioned = db_session.scalars(ordered.offset(200).limit(1)).one()
    spec = parse_query(QueryParams("page[size]=10"), EXAMPLE_QUERY_POLICY)
    raw_cursor = encode_cursor(positioned, spec.sorts, EXAMPLE_QUERY_POLICY)
    assert raw_cursor is not None

    _, statement = _cursor_page_statement(f"{parameter}={raw_cursor}&page[size]=10")
    plan = _explain(db_session, statement)

    assert any("Index Cond: (created_at" in line for line in plan), plan


@pytest.mark.parametrize("offset", [0, 1, 37, 200])
def test_positioned_cursor_page_returns_the_same_rows_as_the_offset_page(
    db_session: Session,
    offset: int,
) -> None:
    """The leading bound is implied by every disjunct, so it must not change the result."""

    _seed_tied_examples(db_session, rows=400, tie_groups=20)
    ordered = apply_sort(select(Example), (), EXAMPLE_QUERY_POLICY)
    all_ids = [model.id for model in db_session.scalars(ordered)]
    positioned = db_session.scalars(ordered.offset(offset).limit(1)).one()
    spec = parse_query(QueryParams("page[size]=10"), EXAMPLE_QUERY_POLICY)
    raw_cursor = encode_cursor(positioned, spec.sorts, EXAMPLE_QUERY_POLICY)
    assert raw_cursor is not None

    _, forward = _cursor_page_statement(f"page[after]={raw_cursor}&page[size]=10")
    _, backward = _cursor_page_statement(f"page[before]={raw_cursor}&page[size]=10")
    forward_ids = [model.id for model in db_session.scalars(forward)]
    backward_ids = [model.id for model in db_session.scalars(backward)][::-1]

    assert forward_ids == all_ids[offset + 1 : offset + 11]
    assert backward_ids == all_ids[max(0, offset - 10) : offset]

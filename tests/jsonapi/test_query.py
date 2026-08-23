"""Allowlisted JSON:API query parser and SQLAlchemy application tests."""

from __future__ import annotations

import json
from base64 import urlsafe_b64encode
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

import pytest
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    Numeric,
    String,
    TypeDecorator,
    func,
    select,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from starlette.datastructures import QueryParams

from app.jsonapi import JsonApiException, LinkObject, SuccessDocument
from app.jsonapi.query import (
    FilterClause,
    FilterField,
    PageCursor,
    PageSpec,
    QueryPolicy,
    QuerySpec,
    SortTerm,
    apply_filters,
    apply_keyset,
    apply_pagination,
    apply_sort,
    build_pagination_links,
    decode_cursor,
    encode_cursor,
    keyset_sorts,
    parse_page_query,
    parse_query,
)
from app.models import Example, ExampleStatus
from app.serializers import ExampleSerializer


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.fixture
def example_query_policy() -> QueryPolicy:
    return QueryPolicy(
        filters={
            "id": FilterField(Example.id, UUID, frozenset({"exact", "in"})),
            "title": FilterField(Example.title, str, frozenset({"exact", "contains", "in"})),
            "description": FilterField(
                Example.description,
                str,
                frozenset({"exact", "contains", "isNull"}),
            ),
            "status": FilterField(Example.status, ExampleStatus, frozenset({"exact", "in"})),
            "score": FilterField(
                Example.score,
                int,
                frozenset({"exact", "gt", "gte", "lt", "lte", "in"}),
            ),
            "createdAt": FilterField(
                Example.created_at,
                _parse_datetime,
                frozenset({"exact", "gt", "gte", "lt", "lte"}),
            ),
        },
        sorts={
            "title": Example.title,
            "score": Example.score,
            "createdAt": Example.created_at,
        },
        includes=frozenset({"category", "tags", "category.examples.tags"}),
        default_sort=(SortTerm("createdAt", descending=True),),
        tie_breaker=SortTerm("id", column=Example.id),
    )


def test_parse_nested_filter_operator(example_query_policy: QueryPolicy) -> None:
    spec = parse_query(QueryParams("filter[score][gte]=80"), example_query_policy)

    assert spec.filters == (FilterClause(name="score", operator="gte", value=80),)


def test_reject_fields_query(example_query_policy: QueryPolicy) -> None:
    with pytest.raises(JsonApiException) as captured:
        parse_query(QueryParams("fields[examples]=title"), example_query_policy)

    assert captured.value.code == "INVALID_QUERY_PARAMETER"
    assert captured.value.source_parameter == "fields[examples]"


def test_parse_sort_include_and_page(example_query_policy: QueryPolicy) -> None:
    spec = parse_query(
        QueryParams("sort=-createdAt,title&include=category,tags&page[number]=2&page[size]=200"),
        example_query_policy,
    )

    assert spec.sorts == (
        SortTerm("createdAt", descending=True),
        SortTerm("title"),
        SortTerm("id"),
    )
    assert spec.includes == ("category", "tags")
    assert spec.page == PageSpec(number=2, size=100)


def test_query_dataclasses_and_policy_mappings_are_immutable(example_query_policy: QueryPolicy) -> None:
    instances_and_fields = [
        (example_query_policy.filters["score"], "operators"),
        (FilterClause("score", "exact", 10), "value"),
        (SortTerm("score"), "descending"),
        (PageSpec(), "number"),
        (example_query_policy, "includes"),
        (example_query_policy, "tie_breaker"),
        (QuerySpec(), "includes"),
    ]

    for instance, field_name in instances_and_fields:
        with pytest.raises(FrozenInstanceError):
            setattr(instance, field_name, None)

    with pytest.raises(TypeError):
        example_query_policy.sorts["unsafe"] = Example.title  # type: ignore[index]

    with pytest.raises(ValueError, match="valid relationship paths"):
        QueryPolicy(
            filters={},
            sorts={},
            includes=frozenset({"category..tags"}),
            default_sort=(),
            tie_breaker=SortTerm("id", column=Example.id),
        )


def test_parse_filter_converts_uuid_enum_datetime_int_and_in_values(
    example_query_policy: QueryPolicy,
) -> None:
    example_id = UUID("00000000-0000-0000-0000-000000000123")
    spec = parse_query(
        QueryParams(
            f"filter[id]={example_id}"
            "&filter[status]=active"
            "&filter[createdAt][gte]=2026-07-14T12:30:00Z"
            "&filter[score][in]=10,20"
        ),
        example_query_policy,
    )

    assert spec.filters == (
        FilterClause("id", "exact", example_id),
        FilterClause("status", "exact", ExampleStatus.ACTIVE),
        FilterClause("createdAt", "gte", datetime(2026, 7, 14, 12, 30, tzinfo=UTC)),
        FilterClause("score", "in", (10, 20)),
    )


def test_parse_all_supported_filter_operators(example_query_policy: QueryPolicy) -> None:
    spec = parse_query(
        QueryParams(
            "filter[title]=alpha"
            "&filter[title][contains]=%_literal"
            "&filter[score][gt]=10"
            "&filter[score][gte]=20"
            "&filter[score][lt]=90"
            "&filter[score][lte]=80"
            "&filter[score][in]=30,40"
            "&filter[description][isNull]=false"
        ),
        example_query_policy,
    )

    assert spec.filters == (
        FilterClause("title", "exact", "alpha"),
        FilterClause("title", "contains", "%_literal"),
        FilterClause("score", "gt", 10),
        FilterClause("score", "gte", 20),
        FilterClause("score", "lt", 90),
        FilterClause("score", "lte", 80),
        FilterClause("score", "in", (30, 40)),
        FilterClause("description", "isNull", False),
    )


@pytest.mark.parametrize(
    ("query", "source_parameter"),
    [
        ("filter[missing]=value", "filter[missing]"),
        ("filter[score][contains]=8", "filter[score][contains]"),
        ("filter[score][unknown]=8", "filter[score][unknown]"),
        ("filter[score]=", "filter[score]"),
        ("filter[score][in]=", "filter[score][in]"),
        ("filter[score][in]=10,,20", "filter[score][in]"),
        ("filter[description][isNull]=TRUE", "filter[description][isNull]"),
        ("filter[score]=not-an-int", "filter[score]"),
        ("filter[id]=not-a-uuid", "filter[id]"),
        ("filter[status]=unknown", "filter[status]"),
        ("filter[createdAt]=not-a-datetime", "filter[createdAt]"),
        ("filter[score]=10&filter[score]=20", "filter[score]"),
        ("filter[score]=10&filter[score][exact]=20", "filter[score][exact]"),
    ],
)
def test_reject_invalid_filters_with_original_source_parameter(
    example_query_policy: QueryPolicy,
    query: str,
    source_parameter: str,
) -> None:
    with pytest.raises(JsonApiException) as captured:
        parse_query(QueryParams(query), example_query_policy)

    assert captured.value.code == "INVALID_FILTER"
    assert captured.value.source_parameter == source_parameter


@pytest.mark.parametrize(
    ("query", "code", "source_parameter"),
    [
        ("unknown=value", "INVALID_QUERY_PARAMETER", "unknown"),
        ("fields[examples]=title", "INVALID_QUERY_PARAMETER", "fields[examples]"),
        ("filter=value", "INVALID_FILTER", "filter"),
        ("filter[]=value", "INVALID_FILTER", "filter[]"),
        ("filter[score][gte", "INVALID_FILTER", "filter[score][gte"),
        ("filter[score][]=10", "INVALID_FILTER", "filter[score][]"),
        ("filter[score][gte][extra]=10", "INVALID_FILTER", "filter[score][gte][extra]"),
        ("sort[field]=title", "INVALID_SORT", "sort[field]"),
        ("include[path]=category", "INVALID_INCLUDE", "include[path]"),
        ("page=1", "INVALID_PAGE", "page"),
        ("page[number][extra]=1", "INVALID_PAGE", "page[number][extra]"),
        ("page[cursor]=one", "INVALID_PAGE", "page[cursor]"),
    ],
)
def test_reject_unknown_or_malformed_query_families(
    example_query_policy: QueryPolicy,
    query: str,
    code: str,
    source_parameter: str,
) -> None:
    with pytest.raises(JsonApiException) as captured:
        parse_query(QueryParams(query), example_query_policy)

    assert captured.value.code == code
    assert captured.value.source_parameter == source_parameter


@pytest.mark.parametrize(
    ("query", "code", "source_parameter"),
    [
        ("sort=", "INVALID_SORT", "sort"),
        ("sort=title,,score", "INVALID_SORT", "sort"),
        ("sort=missing", "INVALID_SORT", "sort"),
        ("sort=title,-title", "INVALID_SORT", "sort"),
        ("sort=title&sort=-score", "INVALID_SORT", "sort"),
        ("include=category,,tags", "INVALID_INCLUDE", "include"),
        ("include=missing", "INVALID_INCLUDE", "include"),
        ("include=category&include=tags", "INVALID_INCLUDE", "include"),
        ("page[number]=0", "INVALID_PAGE", "page[number]"),
        ("page[size]=0", "INVALID_PAGE", "page[size]"),
        ("page[number]=one", "INVALID_PAGE", "page[number]"),
        ("page[size]=1.5", "INVALID_PAGE", "page[size]"),
        ("page[number]=1&page[number]=2", "INVALID_PAGE", "page[number]"),
        ("page[size]=1&page[size]=2", "INVALID_PAGE", "page[size]"),
        (
            "page[number]=9223372036854775807&page[size]=100",
            "INVALID_PAGE",
            "page[number]",
        ),
        ("page[size]=999999999999999999999999", "INVALID_PAGE", "page[size]"),
    ],
)
def test_reject_invalid_sort_include_and_page_values(
    example_query_policy: QueryPolicy,
    query: str,
    code: str,
    source_parameter: str,
) -> None:
    with pytest.raises(JsonApiException) as captured:
        parse_query(QueryParams(query), example_query_policy)

    assert captured.value.code == code
    assert captured.value.source_parameter == source_parameter


def test_empty_include_requests_no_related_resources(
    example_query_policy: QueryPolicy,
) -> None:
    spec = parse_query(QueryParams("include="), example_query_policy)

    assert spec.includes == ()


@pytest.mark.parametrize(
    ("parameter", "raw_value"),
    [
        ("page[number]", "9" * 4_301),
        ("page[size]", "9" * 5_000),
        ("page[number]", str(2**63)),
        ("page[size]", str(2**63)),
    ],
)
def test_reject_page_integer_overflow_without_leaking_python_errors(
    example_query_policy: QueryPolicy,
    parameter: str,
    raw_value: str,
) -> None:
    with pytest.raises(JsonApiException) as captured:
        parse_query(QueryParams([(parameter, raw_value)]), example_query_policy)

    assert captured.value.code == "INVALID_PAGE"
    assert captured.value.source_parameter == parameter


def test_reject_combined_page_offset_overflow(example_query_policy: QueryPolicy) -> None:
    with pytest.raises(JsonApiException) as captured:
        parse_query(
            QueryParams("page[number]=92233720368547760&page[size]=100"),
            example_query_policy,
        )

    assert captured.value.code == "INVALID_PAGE"
    assert captured.value.source_parameter == "page[number]"


def test_default_and_user_sort_are_stable_without_duplicate_tie_breaker(
    example_query_policy: QueryPolicy,
) -> None:
    default_spec = parse_query(QueryParams(), example_query_policy)
    user_spec = parse_query(QueryParams("sort=title"), example_query_policy)

    assert default_spec.sorts == (SortTerm("createdAt", True), SortTerm("id"))
    assert user_spec.sorts == (SortTerm("title"), SortTerm("id"))
    assert all(term.column is None for term in default_spec.sorts)

    with pytest.raises(JsonApiException) as captured:
        parse_query(QueryParams("sort=id"), example_query_policy)

    assert captured.value.code == "INVALID_SORT"
    assert captured.value.source_parameter == "sort"


def test_include_deduplicates_exact_allowlisted_full_paths_in_order(
    example_query_policy: QueryPolicy,
) -> None:
    spec = parse_query(
        QueryParams("include=category.examples.tags,category,category.examples.tags,tags"),
        example_query_policy,
    )

    assert spec.includes == ("category.examples.tags", "category", "tags")
    assert set(spec.includes) <= example_query_policy.includes


def test_filter_name_injection_is_rejected_but_value_is_kept_as_data(
    example_query_policy: QueryPolicy,
) -> None:
    with pytest.raises(JsonApiException) as captured:
        parse_query(QueryParams("filter[title);DROP TABLE examples;--]=value"), example_query_policy)

    assert captured.value.code == "INVALID_FILTER"
    assert captured.value.source_parameter == "filter[title);DROP TABLE examples;--]"

    injection_value = "x' OR 1=1 --"
    spec = parse_query(QueryParams(f"filter[title]={injection_value}"), example_query_policy)
    statement = apply_filters(select(Example), spec.filters, example_query_policy)
    compiled = statement.compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]

    assert injection_value not in str(compiled)
    assert injection_value in compiled.params.values()


@pytest.fixture
def stored_query_examples(db_session: Session) -> tuple[Example, ...]:
    created_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    examples = (
        Example(
            id=UUID(int=1),
            title="alpha%_literal",
            description=None,
            status=ExampleStatus.DRAFT,
            score=40,
            created_at=created_at,
        ),
        Example(
            id=UUID(int=2),
            title="alphaXXliteral",
            description="needle",
            status=ExampleStatus.ACTIVE,
            score=40,
            created_at=created_at,
        ),
        Example(
            id=UUID(int=3),
            title="beta",
            description="other",
            status=ExampleStatus.ARCHIVED,
            score=80,
            created_at=created_at,
        ),
        Example(
            id=UUID(int=4),
            title="x' OR 1=1 --",
            description="needle",
            status=ExampleStatus.ACTIVE,
            score=90,
            created_at=created_at,
        ),
    )
    db_session.add_all(examples)
    db_session.flush()
    return examples


def test_serializer_loaders_are_applied_for_serializer_safety(
    db_session: Session,
    stored_query_examples: tuple[Example, ...],
) -> None:
    model_id = stored_query_examples[0].id
    db_session.expunge_all()
    statement = select(Example).where(Example.id == model_id).options(*ExampleSerializer.loader_options(Example))

    loaded = db_session.scalars(statement).one()
    resource = ExampleSerializer.serialize(loaded)

    assert resource.id == str(model_id)


@pytest.mark.parametrize(
    ("query", "expected_titles"),
    [
        ("filter[score]=40", {"alpha%_literal", "alphaXXliteral"}),
        ("filter[title][contains]=%_", {"alpha%_literal"}),
        ("filter[score][gt]=40", {"beta", "x' OR 1=1 --"}),
        ("filter[score][gte]=90", {"x' OR 1=1 --"}),
        ("filter[score][lt]=80", {"alpha%_literal", "alphaXXliteral"}),
        ("filter[score][lte]=40", {"alpha%_literal", "alphaXXliteral"}),
        ("filter[score][in]=40,80", {"alpha%_literal", "alphaXXliteral", "beta"}),
        ("filter[description][isNull]=true", {"alpha%_literal"}),
        (
            "filter[description][isNull]=false",
            {"alphaXXliteral", "beta", "x' OR 1=1 --"},
        ),
        ("filter[title]=x%27%20OR%201%3D1%20--", {"x' OR 1=1 --"}),
    ],
)
def test_apply_filters_executes_all_operators_with_literal_user_values(
    db_session: Session,
    stored_query_examples: tuple[Example, ...],
    example_query_policy: QueryPolicy,
    query: str,
    expected_titles: set[str],
) -> None:
    del stored_query_examples
    spec = parse_query(QueryParams(query), example_query_policy)
    base_statement = select(Example)
    filtered_statement = apply_filters(base_statement, spec.filters, example_query_policy)

    assert filtered_statement is not base_statement
    assert {example.title for example in db_session.scalars(filtered_statement)} == expected_titles


def test_contains_escapes_sql_wildcards_as_literal_substrings(example_query_policy: QueryPolicy) -> None:
    spec = parse_query(QueryParams("filter[title][contains]=%_"), example_query_policy)
    statement = apply_filters(select(Example), spec.filters, example_query_policy)
    compiled = statement.compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]

    assert "ESCAPE" in str(compiled)
    assert any(value == "/%/_" for value in compiled.params.values())


def test_count_remains_available_before_stable_pagination(
    db_session: Session,
    stored_query_examples: tuple[Example, ...],
    example_query_policy: QueryPolicy,
) -> None:
    del stored_query_examples
    spec = parse_query(
        QueryParams("filter[score]=40&sort=score&page[number]=2&page[size]=1"),
        example_query_policy,
    )
    base_statement = select(Example)
    filtered_statement = apply_filters(base_statement, spec.filters, example_query_policy)
    count_statement = select(func.count()).select_from(filtered_statement.order_by(None).subquery())
    sorted_statement = apply_sort(filtered_statement, spec.sorts, example_query_policy)
    paginated_statement = apply_pagination(sorted_statement, spec.page)

    assert db_session.scalar(count_statement) == 2
    assert [example.id for example in db_session.scalars(paginated_statement)] == [UUID(int=2)]
    assert filtered_statement is not base_statement
    assert sorted_statement is not filtered_statement
    assert paginated_statement is not sorted_statement
    assert filtered_statement._limit_clause is None
    assert len(sorted_statement._order_by_clauses) == 2
    # The probe row is what replaces the count when deciding whether a next page exists.
    first_page = PageSpec(number=1, size=1)
    assert len(list(db_session.scalars(apply_pagination(sorted_statement, first_page)))) == 1
    assert len(list(db_session.scalars(apply_pagination(sorted_statement, first_page, probe=True)))) == 2


@pytest.mark.parametrize(
    ("query", "expected_order_count"),
    [
        ("sort=score", 2),
        ("sort=score,createdAt", 3),
        ("sort=createdAt", 2),
    ],
)
def test_apply_sort_does_not_duplicate_stable_tie_breaker(
    example_query_policy: QueryPolicy,
    query: str,
    expected_order_count: int,
) -> None:
    spec = parse_query(QueryParams(query), example_query_policy)
    statement = apply_sort(select(Example), spec.sorts, example_query_policy)

    assert len(statement._order_by_clauses) == expected_order_count


def test_apply_sort_does_not_duplicate_tie_breaker_by_name_or_column(
    example_query_policy: QueryPolicy,
) -> None:
    explicit_name = apply_sort(select(Example), (SortTerm("id"),), example_query_policy)
    alias_policy = QueryPolicy(
        filters=example_query_policy.filters,
        sorts={**example_query_policy.sorts, "stableId": Example.id},
        includes=example_query_policy.includes,
        default_sort=example_query_policy.default_sort,
        tie_breaker=example_query_policy.tie_breaker,
    )
    alias_spec = parse_query(QueryParams("sort=stableId"), alias_policy)
    explicit_column = apply_sort(select(Example), alias_spec.sorts, alias_policy)

    assert len(explicit_name._order_by_clauses) == 1
    assert alias_spec.sorts == (SortTerm("stableId"),)
    assert len(explicit_column._order_by_clauses) == 1


def _page_number(link: str | LinkObject | None) -> int:
    # build_pagination_links always yields plain URL strings; Links widens the declared type.
    assert isinstance(link, str)
    query = dict(parse_qsl(urlsplit(link).query, keep_blank_values=True))
    return int(query["page[number]"])


def test_pagination_links_preserve_non_page_multi_items_and_encode_brackets() -> None:
    original = QueryParams(
        "filter[status]=active&filter[status]=draft&sort=-createdAt"
        "&include=category&x-extra=one&x-extra=two&page[number]=2&page[size]=2"
    )
    links = build_pagination_links(
        "https://api.example.com/examples?discard=me#private-fragment",
        original,
        PageSpec(number=2, size=2),
        total=5,
    )

    assert "previous" not in links
    assert links["prev"] is not None
    assert links["next"] is not None
    assert _page_number(links["self"]) == 2
    assert _page_number(links["first"]) == 1
    assert _page_number(links["prev"]) == 1
    assert _page_number(links["next"]) == 3
    assert _page_number(links["last"]) == 3

    for link in (links["self"], links["first"], links["prev"], links["next"], links["last"]):
        assert isinstance(link, str)
        parsed = urlsplit(link)
        items = parse_qsl(parsed.query, keep_blank_values=True)
        assert parsed.scheme == ""
        assert parsed.netloc == ""
        assert parsed.fragment == ""
        assert ("filter[status]", "active") in items
        assert ("filter[status]", "draft") in items
        assert items.count(("x-extra", "one")) == 1
        assert items.count(("x-extra", "two")) == 1
        assert "%5B" in link and "%5D" in link
        assert "discard=me" not in link


@pytest.mark.parametrize(
    ("page", "total", "prev", "next_page", "last"),
    [
        (PageSpec(number=1, size=20), 0, None, None, 1),
        (PageSpec(number=1, size=2), 5, None, 2, 3),
        (PageSpec(number=3, size=2), 5, 2, None, 3),
    ],
)
def test_pagination_link_boundaries(
    page: PageSpec,
    total: int,
    prev: int | None,
    next_page: int | None,
    last: int,
) -> None:
    links = build_pagination_links(
        "https://api.example.com/examples",
        QueryParams(),
        page,
        total=total,
    )

    assert links["prev"] is None if prev is None else _page_number(links["prev"]) == prev
    assert links["next"] is None if next_page is None else _page_number(links["next"]) == next_page
    assert _page_number(links["last"]) == last


def test_pagination_links_are_accepted_by_success_document_without_cast() -> None:
    links = build_pagination_links(
        "https://api.example.com/examples",
        QueryParams(),
        PageSpec(),
        total=0,
    )
    document = SuccessDocument(data=[], links=links)

    assert document.links == links
    assert document.links["prev"] is None


def test_parse_page_query_defaults_and_clamps() -> None:
    assert parse_page_query(QueryParams()) == PageSpec(number=1, size=20)
    assert parse_page_query(QueryParams("page[number]=3&page[size]=1000")) == PageSpec(number=3, size=100)


@pytest.mark.parametrize(
    ("query", "code", "source_parameter"),
    [
        ("sort=title", "INVALID_SORT", "sort"),
        ("filter[score]=1", "INVALID_FILTER", "filter[score]"),
        ("include=category", "INVALID_INCLUDE", "include"),
        ("fields[examples]=title", "INVALID_QUERY_PARAMETER", "fields[examples]"),
        ("unknown=value", "INVALID_QUERY_PARAMETER", "unknown"),
        ("page=1", "INVALID_PAGE", "page"),
        ("page[cursor]=one", "INVALID_PAGE", "page[cursor]"),
        ("page[size]=abc", "INVALID_PAGE", "page[size]"),
        ("page[size]=0", "INVALID_PAGE", "page[size]"),
        ("page[size]=1&page[size]=2", "INVALID_PAGE", "page[size]"),
        ("page[number]=9223372036854775807&page[size]=100", "INVALID_PAGE", "page[number]"),
    ],
)
def test_parse_page_query_rejects_non_page_parameters(
    query: str,
    code: str,
    source_parameter: str,
) -> None:
    with pytest.raises(JsonApiException) as captured:
        parse_page_query(QueryParams(query))

    assert captured.value.status_code == 400
    assert captured.value.code == code
    assert captured.value.source_parameter == source_parameter


def _page_query(link: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(link).query, keep_blank_values=True))


def test_pagination_links_null_last_without_totals() -> None:
    without_more = build_pagination_links(
        "https://api.example.com/examples",
        QueryParams("page[number]=2&page[size]=2"),
        PageSpec(number=2, size=2),
        total=None,
        has_more=False,
    )
    with_more = build_pagination_links(
        "https://api.example.com/examples",
        QueryParams("page[number]=2&page[size]=2"),
        PageSpec(number=2, size=2),
        total=None,
        has_more=True,
    )

    assert without_more["last"] is None
    assert without_more["next"] is None
    assert _page_number(without_more["prev"]) == 1
    assert with_more["last"] is None
    assert _page_number(with_more["next"]) == 3
    assert "page[totals]" not in _page_query(str(with_more["self"]))


def test_pagination_links_preserve_the_totals_opt_in() -> None:
    links = build_pagination_links(
        "https://api.example.com/examples",
        QueryParams("page[totals]=true&page[size]=2"),
        PageSpec(number=1, size=2, totals=True),
        total=5,
    )

    assert _page_number(links["last"]) == 3
    for link in links.values():
        assert link is None or _page_query(str(link))["page[totals]"] == "true"


def test_pagination_links_never_contradict_a_paid_for_count() -> None:
    """A page that saw no probe row must still follow the COUNT when one was paid for.

    ``index`` reads the probe on raw rows and may fold some away afterwards, so a false
    ``has_more`` is possible. Letting it win alone would emit ``next: null`` next to a
    ``last`` several pages further on — a document that contradicts itself.
    """

    links = build_pagination_links(
        "https://api.example.com/examples",
        QueryParams("page[totals]=true&page[size]=5"),
        PageSpec(number=1, size=5, totals=True),
        total=90,
        has_more=False,
    )

    assert _page_number(links["last"]) == 18
    assert _page_number(links["next"]) == 2


def test_pagination_links_trust_the_probe_past_the_last_counted_page() -> None:
    """The COUNT must not silence a probe row: rows added after it still need a ``next``."""

    links = build_pagination_links(
        "https://api.example.com/examples",
        QueryParams("page[totals]=true&page[size]=5"),
        PageSpec(number=2, size=5, totals=True),
        total=10,
        has_more=True,
    )

    assert _page_number(links["last"]) == 2
    assert _page_number(links["next"]) == 3


def test_cursor_links_carry_after_and_before_parameters() -> None:
    cursor = PageCursor(raw="Y3Vyc29y", before=False, values=(1,))
    links = build_pagination_links(
        "https://api.example.com/examples",
        QueryParams("page[after]=Y3Vyc29y&page[size]=2"),
        PageSpec(size=2, cursor=cursor),
        total=None,
        has_more=True,
        next_cursor="bmV4dA",
        prev_cursor="cHJldg",
    )

    assert _page_query(str(links["self"]))["page[after]"] == "Y3Vyc29y"
    assert _page_query(str(links["first"]))["page[after]"] == ""
    assert _page_query(str(links["last"]))["page[before]"] == ""
    assert _page_query(str(links["next"]))["page[after]"] == "bmV4dA"
    assert _page_query(str(links["prev"]))["page[before]"] == "cHJldg"
    assert all("%5B" in str(link) and "%5D" in str(link) for link in links.values())
    assert all("page[number]" not in _page_query(str(link)) for link in links.values())


def test_boundary_cursor_links_omit_the_unreachable_direction() -> None:
    start = build_pagination_links(
        "https://api.example.com/examples",
        QueryParams("page[after]=&page[size]=2"),
        PageSpec(size=2, cursor=PageCursor()),
        total=None,
        has_more=True,
        next_cursor="bmV4dA",
        prev_cursor="cHJldg",
    )
    end = build_pagination_links(
        "https://api.example.com/examples",
        QueryParams("page[before]=&page[size]=2"),
        PageSpec(size=2, cursor=PageCursor(before=True)),
        total=None,
        has_more=True,
        next_cursor="bmV4dA",
        prev_cursor="cHJldg",
    )

    assert start["prev"] is None
    assert start["next"] is not None
    assert end["next"] is None
    assert end["prev"] is not None


def test_page_totals_and_cursor_parameters_parse_into_page_spec(
    example_query_policy: QueryPolicy,
) -> None:
    default_spec = parse_query(QueryParams(""), example_query_policy)
    totals_spec = parse_query(QueryParams("page[totals]=true"), example_query_policy)
    disabled_spec = parse_query(QueryParams("page[totals]=false"), example_query_policy)
    after_spec = parse_query(QueryParams("page[after]="), example_query_policy)
    before_spec = parse_query(QueryParams("page[before]="), example_query_policy)

    assert default_spec.page.totals is False
    assert default_spec.page.cursor is None
    assert totals_spec.page.totals is True
    assert disabled_spec.page.totals is False
    assert after_spec.page.cursor == PageCursor(before=False)
    assert before_spec.page.cursor == PageCursor(before=True)


@pytest.mark.parametrize(
    ("query", "parameter"),
    [
        ("page[totals]=1", "page[totals]"),
        ("page[totals]=true&page[totals]=true", "page[totals]"),
        ("page[after]=&page[after]=", "page[after]"),
        ("page[after]=&page[before]=", "page[before]"),
        ("page[after]=&page[number]=3", "page[number]"),
        ("page[after]=not-base64!", "page[after]"),
        ("page[before]=e30", "page[before]"),
    ],
)
def test_reject_unauthorized_page_totals_and_cursors(
    example_query_policy: QueryPolicy,
    query: str,
    parameter: str,
) -> None:
    with pytest.raises(JsonApiException) as captured:
        parse_query(QueryParams(query), example_query_policy)

    assert captured.value.code == "INVALID_PAGE"
    assert captured.value.source_parameter == parameter


def test_cursor_is_rejected_for_a_nullable_sort_column(example_query_policy: QueryPolicy) -> None:
    """A keyset predicate cannot address NULL-ordered rows, so the sort must be NOT NULL."""

    nullable_policy = QueryPolicy(
        filters=example_query_policy.filters,
        sorts={**example_query_policy.sorts, "description": Example.description},
        includes=example_query_policy.includes,
        default_sort=example_query_policy.default_sort,
        tie_breaker=example_query_policy.tie_breaker,
    )

    assert parse_query(QueryParams("sort=description"), nullable_policy).page.cursor is None

    with pytest.raises(JsonApiException) as captured:
        parse_query(QueryParams("sort=description&page[after]="), nullable_policy)

    assert captured.value.code == "INVALID_PAGE"
    assert captured.value.source_parameter == "page[after]"


def _keyset_page(
    session: Session,
    query: str,
    policy: QueryPolicy,
) -> list[Example]:
    spec = parse_query(QueryParams(query), policy)
    cursor = spec.page.cursor
    assert cursor is not None
    statement = apply_sort(
        apply_keyset(select(Example), spec.sorts, policy, cursor),
        keyset_sorts(spec.sorts, cursor),
        policy,
    )
    models = list(session.scalars(apply_pagination(statement, spec.page)))
    if cursor.before:
        models.reverse()
    return models


@pytest.mark.parametrize("sort_query", ["", "sort=-score", "sort=title", "sort=createdAt,score"])
def test_cursor_round_trip_matches_the_equivalent_offset_page(
    db_session: Session,
    stored_query_examples: tuple[Example, ...],
    example_query_policy: QueryPolicy,
    sort_query: str,
) -> None:
    del stored_query_examples
    prefix = f"{sort_query}&" if sort_query else ""
    offset_spec = parse_query(QueryParams(f"{prefix}page[size]=2"), example_query_policy)
    sorted_statement = apply_sort(select(Example), offset_spec.sorts, example_query_policy)
    first_page = list(db_session.scalars(apply_pagination(sorted_statement, offset_spec.page)))
    second_page = list(db_session.scalars(apply_pagination(sorted_statement, PageSpec(number=2, size=2))))

    forward_cursor = encode_cursor(first_page[-1], offset_spec.sorts, example_query_policy)
    backward_cursor = encode_cursor(second_page[0], offset_spec.sorts, example_query_policy)
    assert forward_cursor is not None
    assert backward_cursor is not None

    forward = _keyset_page(
        db_session,
        f"{prefix}page[size]=2&page[after]={forward_cursor}",
        example_query_policy,
    )
    backwards = _keyset_page(
        db_session,
        f"{prefix}page[size]=2&page[before]={backward_cursor}",
        example_query_policy,
    )
    from_start = _keyset_page(db_session, f"{prefix}page[size]=2&page[after]=", example_query_policy)

    assert [model.id for model in forward] == [model.id for model in second_page]
    assert [model.id for model in backwards] == [model.id for model in first_page]
    assert [model.id for model in from_start] == [model.id for model in first_page]


class _CursorBase(DeclarativeBase):
    """Declarative base used only to give the cursor codec real column types.

    The table is never created: the codec reads ``column.type`` and the attribute value,
    so a mapped class is enough to pin the supported type dispatch without touching the
    application schema.
    """


class _Opaque(TypeDecorator[str]):
    """A ``TypeDecorator`` that does not implement ``python_type``, as the base does not.

    ``column.type.python_type`` raises ``NotImplementedError`` here rather than returning
    a type the codec could reject on identity, so the gate has to survive the raise.
    """

    impl = String
    cache_ok = True


class CursorTypes(_CursorBase):
    __tablename__ = "cursor_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flag: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ratio: Mapped[float] = mapped_column(Float, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    moment: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    label: Mapped[str] = mapped_column(String(10), nullable=False)
    key: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[ExampleStatus] = mapped_column(
        SQLEnum(ExampleStatus, name="cursor_status", validate_strings=True),
        nullable=False,
    )
    payload: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    opaque: Mapped[str] = mapped_column(_Opaque(10), nullable=False)


_CURSOR_TYPE_VALUES: dict[str, object] = {
    "id": 7,
    "flag": True,
    "ratio": 1.5,
    "amount": Decimal("12.34"),
    "day": date(2026, 8, 22),
    "moment": datetime(2026, 8, 22, 12, 0, 0, 123456, tzinfo=UTC),
    "label": "라벨",
    "key": UUID(int=9),
    "status": ExampleStatus.ACTIVE,
}


def _cursor_type_policy(name: str) -> QueryPolicy:
    return QueryPolicy(
        filters={},
        sorts={name: getattr(CursorTypes, name)},
        includes=frozenset(),
        default_sort=(SortTerm(name),),
        tie_breaker=SortTerm("id", column=CursorTypes.id),
    )


def _mint_cursor(signature: list[str], values: list[str]) -> str:
    payload = json.dumps({"s": signature, "v": values}, separators=(",", ":"))
    return urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


@pytest.mark.parametrize("name", [name for name in _CURSOR_TYPE_VALUES if name != "id"])
def test_cursor_round_trips_every_supported_column_type(name: str) -> None:
    policy = _cursor_type_policy(name)
    sorts = (SortTerm(name), SortTerm("id"))
    model = CursorTypes(**{name: _CURSOR_TYPE_VALUES[name], "id": _CURSOR_TYPE_VALUES["id"]})

    raw_cursor = encode_cursor(model, sorts, policy)
    assert raw_cursor is not None

    cursor = decode_cursor(raw_cursor, sorts, policy, "page[after]")

    assert cursor.values == (_CURSOR_TYPE_VALUES[name], _CURSOR_TYPE_VALUES["id"])
    assert cursor.before is False


@pytest.mark.parametrize("name", ["payload", "blob"])
def test_cursor_refuses_column_types_it_cannot_represent(name: str) -> None:
    policy = _cursor_type_policy(name)
    sorts = (SortTerm(name), SortTerm("id"))
    model = CursorTypes(**{name: b"x" if name == "blob" else {"a": "b"}, "id": 7})

    assert encode_cursor(model, sorts, policy) is None

    with pytest.raises(JsonApiException) as captured:
        decode_cursor(_mint_cursor([name, "id"], ["x", "7"]), sorts, policy, "page[after]")

    assert captured.value.code == "INVALID_PAGE"


@pytest.mark.parametrize("name", ["payload", "blob", "opaque"])
@pytest.mark.parametrize("parameter", ["page[after]", "page[before]"])
def test_cursor_entry_point_is_refused_for_a_column_the_codec_cannot_represent(
    name: str,
    parameter: str,
) -> None:
    """The gate must consult the codec, not only nullability.

    A NOT NULL column the codec cannot represent used to pass ``_keyset_columns``, so the
    empty boundary cursor was admitted and the request served a dead-end first page:
    ``encode_cursor`` returns ``None``, so ``next`` could never be minted, and any cursor
    minted by hand for that sort is rejected at decode time. Both directions now fail
    loudly at parse time instead.
    """

    policy = _cursor_type_policy(name)

    assert parse_query(QueryParams(f"sort={name}"), policy).page.cursor is None

    with pytest.raises(JsonApiException) as captured:
        parse_query(QueryParams(f"sort={name}&{parameter}="), policy)

    assert captured.value.code == "INVALID_PAGE"
    assert captured.value.source_parameter == parameter


@pytest.mark.parametrize("name", [name for name in _CURSOR_TYPE_VALUES if name != "id"])
def test_cursor_entry_point_is_admitted_for_every_supported_column_type(name: str) -> None:
    """The codec gate must not over-reject: identity dispatch has to cover every codec type."""

    cursor = parse_query(QueryParams(f"sort={name}&page[after]="), _cursor_type_policy(name)).page.cursor

    assert cursor is not None
    assert cursor.values == ()


def test_cursor_refuses_values_its_column_type_cannot_parse() -> None:
    cases = [("flag", "yes"), ("ratio", "many"), ("amount", "much"), ("day", "yesterday"), ("id", "seven")]
    for name, raw_value in cases:
        policy = _cursor_type_policy(name if name != "id" else "label")
        sorts = (SortTerm(name), SortTerm("id")) if name != "id" else (SortTerm("id"),)
        values = [raw_value, "7"] if name != "id" else [raw_value]
        signature = [name, "id"] if name != "id" else ["id"]

        with pytest.raises(JsonApiException) as captured:
            decode_cursor(_mint_cursor(signature, values), sorts, policy, "page[before]")

        assert captured.value.code == "INVALID_PAGE"
        assert captured.value.source_parameter == "page[before]"


def test_cursor_is_not_minted_for_a_model_missing_its_sort_value() -> None:
    policy = _cursor_type_policy("label")
    sorts = (SortTerm("label"), SortTerm("id"))

    assert encode_cursor(CursorTypes(id=7), sorts, policy) is None
    assert encode_cursor(object(), sorts, policy) is None


def _mint_payload(payload: object) -> str:
    return urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")


@pytest.mark.parametrize(
    "raw_cursor",
    [
        "A" * 5_000,
        _mint_payload(["label", "id"]),
        _mint_payload({"s": ["label", "id"], "v": "not-a-list"}),
        _mint_payload({"s": ["label", "id"], "v": ["only-one"]}),
        _mint_payload({"s": ["label", "id"], "v": [7, "7"]}),
        _mint_payload({"s": "label,id", "v": ["a", "7"]}),
    ],
)
def test_cursor_refuses_payloads_that_do_not_match_the_effective_sort(raw_cursor: str) -> None:
    policy = _cursor_type_policy("label")
    sorts = (SortTerm("label"), SortTerm("id"))

    with pytest.raises(JsonApiException) as captured:
        decode_cursor(raw_cursor, sorts, policy, "page[after]")

    assert captured.value.code == "INVALID_PAGE"
    assert captured.value.source_parameter == "page[after]"


def test_cursor_is_not_minted_for_a_sort_no_keyset_can_address(example_query_policy: QueryPolicy) -> None:
    nullable_policy = QueryPolicy(
        filters=example_query_policy.filters,
        sorts={"description": Example.description},
        includes=example_query_policy.includes,
        default_sort=(SortTerm("description"),),
        tie_breaker=example_query_policy.tie_breaker,
    )
    sorts = (SortTerm("description"), SortTerm("id"))

    assert encode_cursor(Example(description="설명"), sorts, nullable_policy) is None

"""Allowlisted JSON:API query parsing and SQLAlchemy application."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, NoReturn
from urllib.parse import urlencode, urlsplit, urlunsplit

from sqlalchemy import Select
from sqlalchemy.orm import InstrumentedAttribute
from starlette.datastructures import QueryParams

from app.jsonapi.documents import Links
from app.jsonapi.errors import ErrorCode, JsonApiException

type FilterOperator = Literal["exact", "contains", "gt", "gte", "lt", "lte", "in", "isNull"]

_FILTER_OPERATORS: frozenset[FilterOperator] = frozenset(
    {"exact", "contains", "gt", "gte", "lt", "lte", "in", "isNull"}
)
_FILTER_PARAMETER_PATTERN = re.compile(r"^filter\[([^\[\]]+)\](?:\[([^\[\]]+)\])?$")
_POSITIVE_INTEGER_PATTERN = re.compile(r"^[0-9]+$")
_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 100
_MAX_SQL_INTEGER = 2**63 - 1
_MAX_SQL_INTEGER_DIGITS = len(str(_MAX_SQL_INTEGER))


@dataclass(frozen=True, slots=True)
class FilterField:
    column: InstrumentedAttribute[Any]
    parser: Callable[[str], object]
    operators: frozenset[FilterOperator]

    def __post_init__(self) -> None:
        operators = frozenset(self.operators)
        if not operators <= _FILTER_OPERATORS:
            raise ValueError("filter field contains an unsupported operator")
        object.__setattr__(self, "operators", operators)


@dataclass(frozen=True, slots=True)
class FilterClause:
    name: str
    operator: FilterOperator
    value: object

    def __post_init__(self) -> None:
        if not self.name or self.operator not in _FILTER_OPERATORS:
            raise ValueError("filter clause must contain a name and supported operator")


@dataclass(frozen=True, slots=True)
class SortTerm:
    name: str
    descending: bool = False
    column: InstrumentedAttribute[Any] | None = field(default=None, compare=False, repr=False, kw_only=True)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("sort term name must not be empty")


@dataclass(frozen=True, slots=True)
class PageSpec:
    number: int = 1
    size: int = _DEFAULT_PAGE_SIZE

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError("page number must be at least one")
        if not 1 <= self.size <= _MAX_PAGE_SIZE:
            raise ValueError("page size must be between one and one hundred")
        if (self.number - 1) * self.size > _MAX_SQL_INTEGER:
            raise ValueError("page offset exceeds the supported SQL integer range")


@dataclass(frozen=True, slots=True)
class QueryPolicy:
    filters: Mapping[str, FilterField]
    sorts: Mapping[str, InstrumentedAttribute[Any]]
    includes: frozenset[str]
    default_sort: Sequence[SortTerm]
    tie_breaker: SortTerm

    def __post_init__(self) -> None:
        filters = MappingProxyType(dict(self.filters))
        sorts = MappingProxyType(dict(self.sorts))
        includes = frozenset(self.includes)
        default_sort = tuple(self.default_sort)

        if self.tie_breaker.column is None:
            raise ValueError("query policy tie breaker must declare its SQLAlchemy column")
        if any(term.name != self.tie_breaker.name and term.name not in sorts for term in default_sort):
            raise ValueError("query policy default sort must contain only allowlisted fields")
        if any(not path or any(not segment for segment in path.split(".")) for path in includes):
            raise ValueError("query policy includes must contain valid relationship paths")

        object.__setattr__(self, "filters", filters)
        object.__setattr__(self, "sorts", sorts)
        object.__setattr__(self, "includes", includes)
        object.__setattr__(self, "default_sort", default_sort)


@dataclass(frozen=True, slots=True)
class QuerySpec:
    filters: tuple[FilterClause, ...] = ()
    sorts: tuple[SortTerm, ...] = ()
    includes: tuple[str, ...] = ()
    page: PageSpec = PageSpec()


def _raise_query_error(code: ErrorCode, source_parameter: str) -> NoReturn:
    raise JsonApiException(
        status_code=400,
        code=code,
        source_parameter=source_parameter,
    )


def _error_code_for_parameter(parameter: str) -> ErrorCode:
    if parameter.startswith("filter"):
        return "INVALID_FILTER"
    if parameter.startswith("sort"):
        return "INVALID_SORT"
    if parameter.startswith("include"):
        return "INVALID_INCLUDE"
    if parameter.startswith("page"):
        return "INVALID_PAGE"
    return "INVALID_QUERY_PARAMETER"


def _parse_filter_value(
    raw_value: str,
    field: FilterField,
    operator: FilterOperator,
    parameter: str,
) -> object:
    if raw_value == "":
        _raise_query_error("INVALID_FILTER", parameter)

    if operator == "isNull":
        if raw_value == "true":
            return True
        if raw_value == "false":
            return False
        _raise_query_error("INVALID_FILTER", parameter)

    try:
        if operator == "in":
            values = raw_value.split(",")
            if not values or any(value == "" for value in values):
                _raise_query_error("INVALID_FILTER", parameter)
            return tuple(field.parser(value) for value in values)
        return field.parser(raw_value)
    except (OverflowError, TypeError, ValueError):
        _raise_query_error("INVALID_FILTER", parameter)


def _parse_filter(
    parameter: str,
    raw_value: str,
    policy: QueryPolicy,
    seen_filters: set[tuple[str, FilterOperator]],
) -> FilterClause:
    match = _FILTER_PARAMETER_PATTERN.fullmatch(parameter)
    if match is None:
        _raise_query_error("INVALID_FILTER", parameter)

    name, raw_operator = match.groups()
    operator = raw_operator or "exact"
    if operator not in _FILTER_OPERATORS:
        _raise_query_error("INVALID_FILTER", parameter)

    field = policy.filters.get(name)
    if field is None or operator not in field.operators:
        _raise_query_error("INVALID_FILTER", parameter)

    filter_key = (name, operator)
    if filter_key in seen_filters:
        _raise_query_error("INVALID_FILTER", parameter)
    seen_filters.add(filter_key)

    value = _parse_filter_value(raw_value, field, operator, parameter)
    return FilterClause(name=name, operator=operator, value=value)


def _append_tie_breaker(terms: Sequence[SortTerm], policy: QueryPolicy) -> tuple[SortTerm, ...]:
    normalized = tuple(SortTerm(term.name, term.descending) for term in terms)
    tie_breaker = policy.tie_breaker
    for term in terms:
        column = term.column if term.column is not None else policy.sorts.get(term.name)
        if term.name == tie_breaker.name or column is tie_breaker.column:
            return normalized
    return (*normalized, SortTerm(tie_breaker.name, tie_breaker.descending))


def _parse_sort(raw_value: str, policy: QueryPolicy) -> tuple[SortTerm, ...]:
    tokens = raw_value.split(",")
    if not tokens or any(token == "" for token in tokens):
        _raise_query_error("INVALID_SORT", "sort")

    terms: list[SortTerm] = []
    seen_names: set[str] = set()
    for token in tokens:
        descending = token.startswith("-")
        name = token[1:] if descending else token
        if not name or name not in policy.sorts or name in seen_names:
            _raise_query_error("INVALID_SORT", "sort")
        seen_names.add(name)
        terms.append(SortTerm(name=name, descending=descending))
    return _append_tie_breaker(terms, policy)


def _parse_include(raw_value: str, policy: QueryPolicy) -> tuple[str, ...]:
    if raw_value == "":
        return ()
    tokens = raw_value.split(",")
    if not tokens or any(token == "" for token in tokens):
        _raise_query_error("INVALID_INCLUDE", "include")

    includes: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token not in policy.includes:
            _raise_query_error("INVALID_INCLUDE", "include")
        if token not in seen:
            seen.add(token)
            includes.append(token)
    return tuple(includes)


def _parse_positive_integer(raw_value: str, parameter: str) -> int:
    if len(raw_value) > _MAX_SQL_INTEGER_DIGITS or _POSITIVE_INTEGER_PATTERN.fullmatch(raw_value) is None:
        _raise_query_error("INVALID_PAGE", parameter)
    try:
        value = int(raw_value)
    except (OverflowError, ValueError):
        _raise_query_error("INVALID_PAGE", parameter)
    if not 1 <= value <= _MAX_SQL_INTEGER:
        _raise_query_error("INVALID_PAGE", parameter)
    return value


def parse_query(query_params: QueryParams, policy: QueryPolicy) -> QuerySpec:
    filters: list[FilterClause] = []
    sorts: tuple[SortTerm, ...] | None = None
    includes: tuple[str, ...] | None = None
    page_number = 1
    page_size = _DEFAULT_PAGE_SIZE
    seen_filters: set[tuple[str, FilterOperator]] = set()
    seen_page_parameters: set[str] = set()

    for parameter, raw_value in query_params.multi_items():
        if parameter.startswith("filter"):
            filters.append(_parse_filter(parameter, raw_value, policy, seen_filters))
            continue

        if parameter == "sort":
            if sorts is not None:
                _raise_query_error("INVALID_SORT", parameter)
            sorts = _parse_sort(raw_value, policy)
            continue

        if parameter == "include":
            if includes is not None:
                _raise_query_error("INVALID_INCLUDE", parameter)
            includes = _parse_include(raw_value, policy)
            continue

        if parameter in {"page[number]", "page[size]"}:
            if parameter in seen_page_parameters:
                _raise_query_error("INVALID_PAGE", parameter)
            seen_page_parameters.add(parameter)
            parsed_page_value = _parse_positive_integer(raw_value, parameter)
            if parameter == "page[number]":
                page_number = parsed_page_value
            else:
                page_size = min(parsed_page_value, _MAX_PAGE_SIZE)
            continue

        _raise_query_error(_error_code_for_parameter(parameter), parameter)

    if (page_number - 1) * page_size > _MAX_SQL_INTEGER:
        _raise_query_error("INVALID_PAGE", "page[number]")

    effective_sorts = sorts or _append_tie_breaker(policy.default_sort, policy)
    return QuerySpec(
        filters=tuple(filters),
        sorts=effective_sorts,
        includes=includes or (),
        page=PageSpec(number=page_number, size=page_size),
    )


def parse_include_query(query_params: QueryParams, policy: QueryPolicy) -> tuple[str, ...]:
    """Parse the only collection-style query parameter valid on a resource URL."""

    includes: tuple[str, ...] | None = None
    for parameter, raw_value in query_params.multi_items():
        if parameter != "include":
            _raise_query_error(_error_code_for_parameter(parameter), parameter)
        if includes is not None:
            _raise_query_error("INVALID_INCLUDE", parameter)
        includes = _parse_include(raw_value, policy)
    return includes or ()


def apply_filters(
    statement: Select[Any],
    filters: Sequence[FilterClause],
    policy: QueryPolicy,
) -> Select[Any]:
    result = statement
    for clause in filters:
        field = policy.filters.get(clause.name)
        parameter = f"filter[{clause.name}]"
        if field is None or clause.operator not in field.operators:
            _raise_query_error("INVALID_FILTER", parameter)

        column = field.column
        if clause.operator == "exact":
            criterion = column == clause.value
        elif clause.operator == "contains":
            if not isinstance(clause.value, str):
                _raise_query_error("INVALID_FILTER", parameter)
            criterion = column.contains(clause.value, autoescape=True)
        elif clause.operator == "gt":
            criterion = column > clause.value
        elif clause.operator == "gte":
            criterion = column >= clause.value
        elif clause.operator == "lt":
            criterion = column < clause.value
        elif clause.operator == "lte":
            criterion = column <= clause.value
        elif clause.operator == "in":
            if not isinstance(clause.value, tuple) or not clause.value:
                _raise_query_error("INVALID_FILTER", parameter)
            criterion = column.in_(clause.value)
        else:
            if not isinstance(clause.value, bool):
                _raise_query_error("INVALID_FILTER", parameter)
            criterion = column.is_(None) if clause.value else column.is_not(None)
        result = result.where(criterion)
    return result


def apply_sort(
    statement: Select[Any],
    sorts: Sequence[SortTerm],
    policy: QueryPolicy,
) -> Select[Any]:
    effective_sorts = _append_tie_breaker(sorts or policy.default_sort, policy)
    order_by = []
    for term in effective_sorts:
        column = policy.tie_breaker.column if term.name == policy.tie_breaker.name else policy.sorts.get(term.name)
        if column is None:
            _raise_query_error("INVALID_SORT", "sort")
        order_by.append(column.desc() if term.descending else column.asc())
    return statement.order_by(*order_by)


def apply_pagination(statement: Select[Any], page: PageSpec) -> Select[Any]:
    return statement.offset((page.number - 1) * page.size).limit(page.size)


def _is_page_parameter(parameter: str) -> bool:
    return parameter == "page" or parameter.startswith("page[")


def build_pagination_links(
    base_url: str,
    query_params: QueryParams,
    page: PageSpec,
    *,
    total: int,
) -> Links:
    if total < 0:
        raise ValueError("pagination total must not be negative")

    parts = urlsplit(base_url)
    preserved_items = [
        (parameter, value) for parameter, value in query_params.multi_items() if not _is_page_parameter(parameter)
    ]
    last_page = max(1, (total + page.size - 1) // page.size)

    def create_link(number: int) -> str:
        query = urlencode(
            [
                *preserved_items,
                ("page[number]", str(number)),
                ("page[size]", str(page.size)),
            ]
        )
        return urlunsplit(("", "", parts.path, query, ""))

    links: Links = {
        "self": create_link(page.number),
        "first": create_link(1),
        "prev": create_link(page.number - 1) if page.number > 1 else None,
        "next": create_link(page.number + 1) if page.number < last_page else None,
        "last": create_link(last_page),
    }
    return links

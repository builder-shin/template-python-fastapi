"""Allowlisted JSON:API query parsing and SQLAlchemy application."""

from __future__ import annotations

import json
import re
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal, NoReturn
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, or_
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
_CURSOR_PARAMETERS = ("page[after]", "page[before]")
_PAGE_PARAMETERS = frozenset({"page[number]", "page[size]", "page[totals]", *_CURSOR_PARAMETERS})
_MAX_CURSOR_LENGTH = 4096
_CURSOR_PYTHON_TYPES: frozenset[type] = frozenset({bool, int, float, Decimal, str, UUID, datetime, date})


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
class PageCursor:
    """Opaque keyset position decoded against the effective sort of a request.

    An empty ``raw`` is the entry point into cursor mode: ``page[after]=`` addresses the
    start of the collection and ``page[before]=`` its end, so a client can walk the whole
    collection without ever issuing an OFFSET.
    """

    raw: str = ""
    before: bool = False
    values: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        if bool(self.raw) != bool(self.values):
            raise ValueError("page cursor must carry sort values unless it addresses a boundary")


@dataclass(frozen=True, slots=True)
class PageSpec:
    number: int = 1
    size: int = _DEFAULT_PAGE_SIZE
    cursor: PageCursor | None = None
    totals: bool = False

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError("page number must be at least one")
        if not 1 <= self.size <= _MAX_PAGE_SIZE:
            raise ValueError("page size must be between one and one hundred")
        if (self.number - 1) * self.size > _MAX_SQL_INTEGER:
            raise ValueError("page offset exceeds the supported SQL integer range")
        if self.cursor is not None and self.number != 1:
            raise ValueError("a keyset cursor cannot be combined with a page number")


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


def _sort_signature(sorts: Sequence[SortTerm]) -> list[str]:
    return [f"-{term.name}" if term.descending else term.name for term in sorts]


def _resolve_sort_column(term: SortTerm, policy: QueryPolicy) -> InstrumentedAttribute[Any] | None:
    if term.name == policy.tie_breaker.name:
        return policy.tie_breaker.column
    return policy.sorts.get(term.name)


def _is_cursor_representable(column: InstrumentedAttribute[Any]) -> bool:
    """Report whether the cursor codec can round-trip ``column``'s Python type.

    Membership is tested by identity because ``_decode_cursor_value`` dispatches with
    ``is``: ``bool`` is a subclass of ``int`` and ``datetime`` of ``date``, so a subclass
    test would admit types the decoder never actually handles.
    """

    try:
        python_type = column.type.python_type
    except (AttributeError, NotImplementedError):
        return False
    if isinstance(python_type, type) and issubclass(python_type, Enum):
        return True
    return python_type in _CURSOR_PYTHON_TYPES


def _keyset_columns(
    sorts: Sequence[SortTerm],
    policy: QueryPolicy,
    parameter: str,
) -> tuple[tuple[SortTerm, InstrumentedAttribute[Any]], ...]:
    """Resolve every effective sort term to a NOT NULL, cursor-representable column.

    A lexicographic ``WHERE`` cannot address rows whose sort value is NULL, because every
    comparison against NULL is unknown. Refusing a nullable sort column is therefore the
    only way to keep cursor pages from silently skipping rows.

    The column must also be one the cursor codec can round-trip. Gating only on
    nullability would admit a sort whose boundary cursor parses, yet whose ``next`` link
    can never be minted (``encode_cursor`` returns ``None``) and whose hand-minted cursor
    is rejected at decode time — a first page that dead-ends instead of failing loudly.
    """

    if not sorts:
        _raise_query_error("INVALID_PAGE", parameter)

    columns: list[tuple[SortTerm, InstrumentedAttribute[Any]]] = []
    for term in sorts:
        column = _resolve_sort_column(term, policy)
        if column is None or bool(getattr(column.expression, "nullable", True)) or not _is_cursor_representable(column):
            _raise_query_error("INVALID_PAGE", parameter)
        columns.append((term, column))
    return tuple(columns)


def _encode_cursor_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, str | int | float | Decimal | UUID):
        return str(value)
    return None


def _decode_cursor_value(column: InstrumentedAttribute[Any], raw_value: str, parameter: str) -> object:
    try:
        python_type = column.type.python_type
    except (AttributeError, NotImplementedError):
        _raise_query_error("INVALID_PAGE", parameter)

    try:
        if isinstance(python_type, type) and issubclass(python_type, Enum):
            return python_type(raw_value)
        if python_type is bool:
            if raw_value not in {"true", "false"}:
                _raise_query_error("INVALID_PAGE", parameter)
            return raw_value == "true"
        if python_type is int:
            return int(raw_value)
        if python_type is float:
            return float(raw_value)
        if python_type is Decimal:
            return Decimal(raw_value)
        if python_type is str:
            return raw_value
        if python_type is UUID:
            return UUID(raw_value)
        if python_type is datetime:
            return datetime.fromisoformat(raw_value)
        if python_type is date:
            return date.fromisoformat(raw_value)
    except (ArithmeticError, TypeError, ValueError):
        _raise_query_error("INVALID_PAGE", parameter)
    _raise_query_error("INVALID_PAGE", parameter)


def encode_cursor(model: object, sorts: Sequence[SortTerm], policy: QueryPolicy) -> str | None:
    """Mint an opaque cursor for ``model`` under the request's effective sort."""

    try:
        columns = _keyset_columns(sorts, policy, _CURSOR_PARAMETERS[0])
    except JsonApiException:
        return None

    values: list[str] = []
    for _, column in columns:
        encoded = _encode_cursor_value(getattr(model, column.key, None))
        if encoded is None:
            return None
        values.append(encoded)

    payload = json.dumps({"s": _sort_signature(sorts), "v": values}, separators=(",", ":"))
    return urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(
    raw_cursor: str,
    sorts: Sequence[SortTerm],
    policy: QueryPolicy,
    parameter: str,
) -> PageCursor:
    """Decode an opaque cursor, rejecting anything the effective sort cannot address."""

    columns = _keyset_columns(sorts, policy, parameter)
    before = parameter == _CURSOR_PARAMETERS[1]
    if not raw_cursor:
        return PageCursor(before=before)
    if len(raw_cursor) > _MAX_CURSOR_LENGTH:
        _raise_query_error("INVALID_PAGE", parameter)

    try:
        decoded = urlsafe_b64decode(raw_cursor + "=" * (-len(raw_cursor) % 4))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        _raise_query_error("INVALID_PAGE", parameter)

    if not isinstance(payload, dict):
        _raise_query_error("INVALID_PAGE", parameter)
    signature = payload.get("s")
    raw_values = payload.get("v")
    if not isinstance(signature, list) or signature != _sort_signature(sorts):
        _raise_query_error("INVALID_PAGE", parameter)
    if not isinstance(raw_values, list) or len(raw_values) != len(columns):
        _raise_query_error("INVALID_PAGE", parameter)

    values: list[object] = []
    for (_, column), raw_value in zip(columns, raw_values, strict=True):
        if not isinstance(raw_value, str):
            _raise_query_error("INVALID_PAGE", parameter)
        values.append(_decode_cursor_value(column, raw_value, parameter))

    return PageCursor(raw=raw_cursor, before=before, values=tuple(values))


def parse_query(query_params: QueryParams, policy: QueryPolicy) -> QuerySpec:
    filters: list[FilterClause] = []
    sorts: tuple[SortTerm, ...] | None = None
    includes: tuple[str, ...] | None = None
    page_number = 1
    page_size = _DEFAULT_PAGE_SIZE
    page_totals = False
    raw_cursors: dict[str, str] = {}
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

        if parameter in _PAGE_PARAMETERS:
            if parameter in seen_page_parameters:
                _raise_query_error("INVALID_PAGE", parameter)
            seen_page_parameters.add(parameter)
            if parameter == "page[totals]":
                if raw_value not in {"true", "false"}:
                    _raise_query_error("INVALID_PAGE", parameter)
                page_totals = raw_value == "true"
            elif parameter in _CURSOR_PARAMETERS:
                if raw_cursors:
                    _raise_query_error("INVALID_PAGE", parameter)
                raw_cursors[parameter] = raw_value
            else:
                parsed_page_value = _parse_positive_integer(raw_value, parameter)
                if parameter == "page[number]":
                    page_number = parsed_page_value
                else:
                    page_size = min(parsed_page_value, _MAX_PAGE_SIZE)
            continue

        _raise_query_error(_error_code_for_parameter(parameter), parameter)

    if (page_number - 1) * page_size > _MAX_SQL_INTEGER:
        _raise_query_error("INVALID_PAGE", "page[number]")
    if raw_cursors and "page[number]" in seen_page_parameters:
        _raise_query_error("INVALID_PAGE", "page[number]")

    effective_sorts = sorts or _append_tie_breaker(policy.default_sort, policy)
    cursor = next(
        (decode_cursor(value, effective_sorts, policy, parameter) for parameter, value in raw_cursors.items()),
        None,
    )
    return QuerySpec(
        filters=tuple(filters),
        sorts=effective_sorts,
        includes=includes or (),
        page=PageSpec(number=page_number, size=page_size, cursor=cursor, totals=page_totals),
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


def parse_page_query(query_params: QueryParams) -> PageSpec:
    """Parse the only collection-style query parameters valid on a related-resource URL."""

    page_number = 1
    page_size = _DEFAULT_PAGE_SIZE
    seen_page_parameters: set[str] = set()

    for parameter, raw_value in query_params.multi_items():
        if parameter not in {"page[number]", "page[size]"}:
            _raise_query_error(_error_code_for_parameter(parameter), parameter)
        if parameter in seen_page_parameters:
            _raise_query_error("INVALID_PAGE", parameter)
        seen_page_parameters.add(parameter)
        parsed_page_value = _parse_positive_integer(raw_value, parameter)
        if parameter == "page[number]":
            page_number = parsed_page_value
        else:
            page_size = min(parsed_page_value, _MAX_PAGE_SIZE)

    if (page_number - 1) * page_size > _MAX_SQL_INTEGER:
        _raise_query_error("INVALID_PAGE", "page[number]")
    return PageSpec(number=page_number, size=page_size)


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


def keyset_sorts(sorts: Sequence[SortTerm], cursor: PageCursor | None) -> tuple[SortTerm, ...]:
    """Return the ORDER BY terms to execute for ``cursor``.

    A ``page[before]`` cursor walks the collection backwards, so the database has to
    order in the inverted direction for ``LIMIT`` to cut the right end; the caller
    re-reverses the rows before serializing them.
    """

    if cursor is None or not cursor.before:
        return tuple(sorts)
    return tuple(SortTerm(term.name, not term.descending, column=term.column) for term in sorts)


def apply_keyset(
    statement: Select[Any],
    sorts: Sequence[SortTerm],
    policy: QueryPolicy,
    cursor: PageCursor,
) -> Select[Any]:
    """Restrict ``statement`` to the rows after (or before) the cursor position.

    Mixed ascending/descending sorts rule out PostgreSQL row-value comparison, so the
    lexicographic predicate is expanded as ``OR`` of ``AND`` prefixes instead. A bare
    ``OR`` is not sargable: the planner cannot start an index scan from it, so the page
    reads and discards every row before the cursor — exactly the deep-OFFSET cost the
    cursor mode exists to avoid. Conjoining a non-strict bound on the leading sort column
    gives the planner an ``Index Cond`` start point. Every disjunct already implies the
    bound (the first is strict on the leading column, the rest pin it to equality), so
    the result set is unchanged.
    """

    if not cursor.values:
        return statement

    parameter = _CURSOR_PARAMETERS[1] if cursor.before else _CURSOR_PARAMETERS[0]
    columns = _keyset_columns(sorts, policy, parameter)
    if len(columns) != len(cursor.values):
        _raise_query_error("INVALID_PAGE", parameter)

    clauses: list[ColumnElement[bool]] = []
    for index, (term, column) in enumerate(columns):
        prefix = [columns[position][1] == cursor.values[position] for position in range(index)]
        value = cursor.values[index]
        descending = term.descending != cursor.before
        comparison = column < value if descending else column > value
        clauses.append(and_(*prefix, comparison))

    leading_term, leading_column = columns[0]
    leading_value = cursor.values[0]
    leading_descending = leading_term.descending != cursor.before
    leading_bound = leading_column <= leading_value if leading_descending else leading_column >= leading_value
    return statement.where(and_(leading_bound, or_(*clauses)))


def apply_pagination(statement: Select[Any], page: PageSpec, *, probe: bool = False) -> Select[Any]:
    """Limit ``statement`` to one page, optionally fetching one extra probe row.

    The probe row is what tells the caller whether a ``next`` page exists without
    paying for a COUNT.
    """

    limit = page.size + 1 if probe else page.size
    if page.cursor is not None:
        return statement.limit(limit)
    return statement.offset((page.number - 1) * page.size).limit(limit)


def _is_page_parameter(parameter: str) -> bool:
    return parameter == "page" or parameter.startswith("page[")


def build_pagination_links(
    base_url: str,
    query_params: QueryParams,
    page: PageSpec,
    *,
    total: int | None,
    has_more: bool | None = None,
    next_cursor: str | None = None,
    prev_cursor: str | None = None,
) -> Links:
    """Build the pagination links for one collection page.

    ``total`` is optional: without it ``last`` is null and ``next`` follows ``has_more``,
    which the caller derives from a probe row instead of a COUNT. When ``has_more`` is
    omitted it falls back to the ``total`` arithmetic, which keeps the pure offset
    callers unchanged.

    When both are available the two are OR-ed rather than letting the probe win alone: a
    page whose probe row was folded away (a de-duplicated row-multiplying scope) would
    otherwise emit ``next: null`` next to a ``last`` that the COUNT puts several pages
    further on, which is a self-contradictory document.
    """

    if total is not None and total < 0:
        raise ValueError("pagination total must not be negative")

    parts = urlsplit(base_url)
    preserved_items = [
        (parameter, value) for parameter, value in query_params.multi_items() if not _is_page_parameter(parameter)
    ]
    totals_items = [("page[totals]", "true")] if page.totals else []
    last_page = None if total is None else max(1, (total + page.size - 1) // page.size)

    def create_link(*page_items: tuple[str, str]) -> str:
        query = urlencode(
            [
                *preserved_items,
                *totals_items,
                *page_items,
                ("page[size]", str(page.size)),
            ]
        )
        return urlunsplit(("", "", parts.path, query, ""))

    def create_offset_link(number: int) -> str:
        return create_link(("page[number]", str(number)))

    cursor = page.cursor
    if cursor is None:
        count_more = last_page is not None and page.number < last_page
        more = count_more if has_more is None else (has_more or count_more)
        offset_links: Links = {
            "self": create_offset_link(page.number),
            "first": create_offset_link(1),
            "prev": create_offset_link(page.number - 1) if page.number > 1 else None,
            "next": create_offset_link(page.number + 1) if more else None,
            "last": None if last_page is None else create_offset_link(last_page),
        }
        return offset_links

    forward = not cursor.before
    more = bool(has_more)
    positioned = bool(cursor.values)
    self_parameter = _CURSOR_PARAMETERS[0] if forward else _CURSOR_PARAMETERS[1]
    emit_next = more if forward else positioned
    emit_prev = positioned if forward else more
    cursor_links: Links = {
        "self": create_link((self_parameter, cursor.raw)),
        "first": create_link((_CURSOR_PARAMETERS[0], "")),
        "prev": create_link((_CURSOR_PARAMETERS[1], prev_cursor)) if emit_prev and prev_cursor else None,
        "next": create_link((_CURSOR_PARAMETERS[0], next_cursor)) if emit_next and next_cursor else None,
        "last": create_link((_CURSOR_PARAMETERS[1], "")),
    }
    return cursor_links

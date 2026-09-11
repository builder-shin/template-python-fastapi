"""Values must be checked before they reach PostgreSQL cursor bindings."""

import json
from base64 import urlsafe_b64encode

import pytest
from starlette.datastructures import QueryParams

from app.jsonapi.errors import JsonApiException
from app.jsonapi.query import parse_query
from app.schemas.example import EXAMPLE_QUERY_POLICY


@pytest.mark.parametrize("direction", ["after", "before"])
@pytest.mark.parametrize(
    "sort,value",
    [
        ("score", "2147483648"),
        ("score", "-2147483649"),
        ("createdAt", "0001-01-01T00:00:00+01:00"),
        ("createdAt", "9999-12-31T23:59:59-01:00"),
        ("createdAt", "2026-01-01T00:00:00"),
    ],
)
def test_cursor_rejects_unsafe_database_values(direction: str, sort: str, value: str) -> None:
    payload = {"s": [sort, "id"], "v": [value, "00000000-0000-4000-8000-000000000001"]}
    cursor = urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    with pytest.raises(JsonApiException):
        parse_query(QueryParams({"sort": sort, f"page[{direction}]": cursor}), EXAMPLE_QUERY_POLICY)


@pytest.mark.parametrize("value", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_filter_rejects_utc_outside_supported_calendar(value: str) -> None:
    with pytest.raises(JsonApiException):
        parse_query(QueryParams({"filter[createdAt][gte]": value}), EXAMPLE_QUERY_POLICY)

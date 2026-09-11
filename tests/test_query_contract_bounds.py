"""HTTP regression coverage for values outside PostgreSQL integer bindings."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("value", ["2147483648", "-2147483649", "9" * 310, "3.5"])
def test_score_filter_rejects_values_outside_sql_integer(client: TestClient, value: str) -> None:
    response = client.get("/api/v1/examples", params={"filter[score][gte]": value})
    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "INVALID_FILTER"
    assert response.json()["errors"][0]["source"] == {"parameter": "filter[score][gte]"}


@pytest.mark.parametrize("number", ["9007199254740993", "9" * 36])
def test_offset_rejects_inexact_cross_backend_arithmetic(client: TestClient, number: str) -> None:
    response = client.get("/api/v1/examples", params={"page[number]": number, "page[size]": "1"})
    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "INVALID_PAGE"
    assert response.json()["errors"][0]["source"] == {"parameter": "page[number]"}


def test_sql_integer_boundaries_are_valid(client: TestClient) -> None:
    response = client.get(
        "/api/v1/examples",
        params={"filter[score][gte]": "-2147483648", "page[number]": "2147483648", "page[size]": "1"},
    )
    assert response.status_code == 200
    assert response.json()["data"] == []

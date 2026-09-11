"""Regressions for JSON integer semantics and strict resource documents."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("score", [42, 42.0, 4.2e1])
def test_integral_json_numbers(authenticated_client: TestClient, score: int | float) -> None:
    response = authenticated_client.post(
        "/api/v1/examples",
        json={"data": {"type": "examples", "attributes": {"title": " ", "status": "active", "score": score}}},
        headers={"Content-Type": "application/vnd.api+json"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["attributes"]["score"] == 42


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", 123),
        ("title", True),
        ("title", None),
        ("description", 123),
        ("description", False),
        ("score", "42"),
        ("score", True),
        ("score", None),
        ("status", 0),
        ("title", "x" * 201),
    ],
)
def test_strict_write_attribute_types(authenticated_client: TestClient, field: str, value: object) -> None:
    attributes = {"title": "Audit", "status": "active", "score": 42, field: value}
    response = authenticated_client.post(
        "/api/v1/examples",
        json={"data": {"type": "examples", "attributes": attributes}},
        headers={"Content-Type": "application/vnd.api+json"},
    )
    assert response.status_code == 422
    assert [(error["code"], error["source"]["pointer"]) for error in response.json()["errors"]] == [
        ("VALIDATION_ERROR", f"/data/attributes/{field}")
    ]


def test_missing_put_members_are_complete(authenticated_client: TestClient) -> None:
    response = authenticated_client.put(
        "/api/v1/examples/00000000-0000-4000-8000-000000000099",
        json={"data": {"attributes": {}}},
        headers={"Content-Type": "application/vnd.api+json"},
    )
    assert response.status_code == 422
    assert sorted(error["source"]["pointer"] for error in response.json()["errors"]) == [
        "/data/attributes/score",
        "/data/attributes/status",
        "/data/attributes/title",
        "/data/id",
        "/data/type",
    ]

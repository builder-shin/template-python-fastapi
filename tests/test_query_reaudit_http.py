"""Round trip every supported sort using real PostgreSQL rows."""

import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Example, ExampleStatus


@pytest.mark.parametrize(
    "sort",
    ["title", "-title", "score", "-score", "status", "-status", "createdAt", "-createdAt", "updatedAt", "-updatedAt"],
)
def test_all_sorts_roundtrip_and_validate_boundaries(client: TestClient, committed_session: Session, sort: str) -> None:
    for index in range(6):
        committed_session.add(
            Example(
                id=uuid4(),
                title="query-roundtrip",
                status=list(ExampleStatus)[index // 2],
                score=index // 2,
                created_at=datetime(2026, 1, 1, microsecond=123450 + index, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, microsecond=123450 + index, tzinfo=UTC),
            )
        )
    committed_session.commit()
    expected = client.get("/api/v1/examples", params={"sort": sort, "page[size]": "100"}).json()["data"]
    if sort.lstrip("-") == "status":
        statuses = [row["attributes"]["status"] for row in expected]
        assert statuses == (
            ["draft", "draft", "active", "active", "archived", "archived"]
            if sort == "status"
            else ["archived", "archived", "active", "active", "draft", "draft"]
        )
    for direction in ["after", "before"]:
        response = client.get("/api/v1/examples", params={"sort": sort, f"page[{direction}]": "", "page[size]": "1"})
        rows = []
        for _ in range(8):
            assert response.status_code == 200
            rows.extend(response.json()["data"])
            link = response.json()["links"]["next" if direction == "after" else "prev"]
            if link is None:
                break
            response = client.get(link)
        assert rows == (expected if direction == "after" else expected[::-1])
    first = client.get("/api/v1/examples", params={"sort": sort, "page[after]": "", "page[size]": "1"})
    raw = parse_qs(urlsplit(first.json()["links"]["next"]).query)["page[after]"][0]
    payload = json.loads(urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    invalid: list[object] = [None, True, 1, {}, []]
    if "score" in sort:
        invalid += ["2147483648", "-2147483649", "not-a-number"]
    if "At" in sort:
        invalid += ["0000-01-01T00:00:00Z", "0001-01-01T00:00:00+01:00", "2026-02-30T00:00:00Z"]
    if "status" in sort:
        invalid += ["unknown"]
    for direction in ["after", "before"]:
        for value in invalid:
            payload["v"][0] = value
            cursor = urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
            response = client.get("/api/v1/examples", params={"sort": sort, f"page[{direction}]": cursor})
            assert response.status_code == 400
            assert response.json()["errors"][0]["source"] == {"parameter": f"page[{direction}]"}

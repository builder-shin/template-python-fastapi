"""Read-only reference resource route integration tests."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.jsonapi import JSONAPI_MEDIA_TYPE
from app.models import ExampleCategory, ExampleTag

HEADERS = {"Accept": JSONAPI_MEDIA_TYPE}


def _persist_reference_data(session: Session) -> None:
    session.add_all(
        [
            # 이름을 ASCII로 두는 이유: 정렬 기대값이 PostgreSQL의 대조 규칙에
            # 의존하지 않게 하려는 것이다. 한글 이름을 쓰면 DB locale에 따라
            # 순서가 파이썬 sorted()와 갈릴 수 있다.
            ExampleCategory(name="alpha"),
            ExampleCategory(name="beta"),
            ExampleCategory(name="gamma"),
            ExampleTag(name="draft-only"),
            ExampleTag(name="public"),
        ]
    )
    session.commit()


def test_categories_collection_is_sorted_by_name(
    client: TestClient,
    committed_session: Session,
) -> None:
    _persist_reference_data(committed_session)

    response = client.get("/api/v1/categories", headers=HEADERS)

    assert response.status_code == 200
    document = response.json()
    names = [resource["attributes"]["name"] for resource in document["data"]]
    assert names == sorted(names)
    assert all(resource["type"] == "exampleCategories" for resource in document["data"])


def test_categories_support_explicit_sort_by_name_in_both_directions(
    client: TestClient,
    committed_session: Session,
) -> None:
    """``sort=name``/``sort=-name`` must resolve through the ``sorts`` mapping, not just
    ``default_sort``.

    Every other test in this module either relies on the default sort or ties every row's
    ``createdAt`` to the same commit, so a typo in ``EXAMPLE_CATEGORY_QUERY_POLICY.sorts``
    (for example mapping ``"createdAt"`` to ``ExampleCategory.name``) would go unnoticed.
    ``name`` is unique, so both directions are fully determined by name alone — nothing here
    depends on the ``id`` tie breaker.
    """

    _persist_reference_data(committed_session)

    ascending = client.get("/api/v1/categories?sort=name", headers=HEADERS)
    descending = client.get("/api/v1/categories?sort=-name", headers=HEADERS)

    assert ascending.status_code == 200
    assert descending.status_code == 200
    assert [resource["attributes"]["name"] for resource in ascending.json()["data"]] == [
        "alpha",
        "beta",
        "gamma",
    ]
    assert [resource["attributes"]["name"] for resource in descending.json()["data"]] == [
        "gamma",
        "beta",
        "alpha",
    ]


def test_category_single_resource_carries_self_link(
    client: TestClient,
    committed_session: Session,
) -> None:
    _persist_reference_data(committed_session)
    category_id = client.get("/api/v1/categories", headers=HEADERS).json()["data"][0]["id"]

    response = client.get(f"/api/v1/categories/{category_id}", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()["data"]["links"]["self"] == f"/api/v1/categories/{category_id}"


def test_categories_support_the_declared_name_filters(
    client: TestClient,
    committed_session: Session,
) -> None:
    _persist_reference_data(committed_session)

    exact = client.get("/api/v1/categories?filter[name]=beta", headers=HEADERS)
    contains = client.get("/api/v1/categories?filter[name][contains]=et", headers=HEADERS)

    assert exact.status_code == 200
    assert [resource["attributes"]["name"] for resource in exact.json()["data"]] == ["beta"]
    assert contains.status_code == 200
    assert [resource["attributes"]["name"] for resource in contains.json()["data"]] == ["beta"]


def test_categories_walk_the_whole_collection_by_cursor(
    client: TestClient,
    committed_session: Session,
) -> None:
    _persist_reference_data(committed_session)

    seen: list[str] = []
    url: str | None = "/api/v1/categories?page[size]=2&page[after]="
    while url is not None:
        document = client.get(url, headers=HEADERS).json()
        seen.extend(resource["attributes"]["name"] for resource in document["data"])
        url = document["links"].get("next")

    assert seen == ["alpha", "beta", "gamma"]


def test_categories_reject_an_undeclared_filter_operator(client: TestClient) -> None:
    response = client.get("/api/v1/categories?filter[name][gt]=a", headers=HEADERS)

    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "INVALID_FILTER"


def test_categories_reject_an_undeclared_include(client: TestClient) -> None:
    response = client.get("/api/v1/categories?include=examples", headers=HEADERS)

    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "INVALID_INCLUDE"


def test_categories_do_not_accept_writes(client: TestClient) -> None:
    response = client.post(
        "/api/v1/categories",
        headers={**HEADERS, "Content-Type": JSONAPI_MEDIA_TYPE},
        content=b'{"data":{"type":"exampleCategories","attributes":{"name":"delta"}}}',
    )

    assert response.status_code == 405
    assert response.json()["errors"][0]["code"] == "HTTP_ERROR"


def test_tags_collection_is_sorted_by_name(
    client: TestClient,
    committed_session: Session,
) -> None:
    _persist_reference_data(committed_session)

    response = client.get("/api/v1/tags", headers=HEADERS)

    assert response.status_code == 200
    document = response.json()
    names = [resource["attributes"]["name"] for resource in document["data"]]
    assert names == sorted(names)
    assert all(resource["type"] == "exampleTags" for resource in document["data"])


def test_tags_do_not_accept_writes(client: TestClient) -> None:
    response = client.delete(
        "/api/v1/tags/00000000-0000-0000-0000-000000000000",
        headers=HEADERS,
    )

    assert response.status_code == 405
    assert response.json()["errors"][0]["code"] == "HTTP_ERROR"

"""Serializer-declared JSON:API relationship action tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from threading import Barrier
from types import MappingProxyType
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.controllers.concerns.crud_actions import CrudActions
from app.jsonapi import JSONAPI_MEDIA_TYPE
from app.models import Example, ExampleCategory, ExampleStatus, ExampleTag
from app.schemas.example import (
    EXAMPLE_QUERY_POLICY,
    ExampleCreate,
    ExampleRelationships,
    ExampleReplace,
    ExampleUpdate,
)
from app.serializers import ExampleSerializer
from app.serializers.base import JsonApiSerializer, RelationshipDefinition


class RelationshipController(CrudActions[Example, ExampleCreate, ExampleUpdate, ExampleReplace]):
    model_class = Example
    serializer_class = ExampleSerializer
    create_schema = ExampleCreate
    update_schema = ExampleUpdate
    replace_schema = ExampleReplace
    relationships_schema: type[BaseModel] | None = ExampleRelationships
    query_policy = EXAMPLE_QUERY_POLICY


class ReadOnlyRelationshipController(RelationshipController):
    relationships_schema = None


class MiniExampleSerializer(JsonApiSerializer[Example]):
    """Minimal example serializer used as a relationship target of the nested fixtures."""

    type_name = "examples"
    resource_path = None
    attributes = ("title",)


class NestedCategorySerializer(JsonApiSerializer[ExampleCategory]):
    """Category serializer that declares a relationship of its own back to examples."""

    type_name = "exampleCategories"
    resource_path = None
    attributes = ("name",)
    relationships = MappingProxyType(
        {
            "examples": RelationshipDefinition(
                attribute="examples",
                serializer=MiniExampleSerializer,
                many=True,
            )
        }
    )


class NestedTagSerializer(JsonApiSerializer[ExampleTag]):
    """Tag serializer that declares a relationship of its own back to examples."""

    type_name = "exampleTags"
    resource_path = None
    attributes = ("name",)
    relationships = MappingProxyType(
        {
            "examples": RelationshipDefinition(
                attribute="examples",
                serializer=MiniExampleSerializer,
                many=True,
            )
        }
    )


class NestedExampleSerializer(ExampleSerializer):
    """Owning serializer whose relationship targets declare relationships themselves."""

    relationships = MappingProxyType(
        {
            "category": RelationshipDefinition(
                attribute="category",
                serializer=NestedCategorySerializer,
                many=False,
                linkage_attribute="category_id",
            ),
            "tags": RelationshipDefinition(
                attribute="tags",
                serializer=NestedTagSerializer,
                many=True,
            ),
        }
    )


class NestedRelationshipController(RelationshipController):
    serializer_class = NestedExampleSerializer


relationship_dependency_log: list[str] = []


def record_relationship_read_dependency() -> None:
    relationship_dependency_log.append("read")


def record_relationship_write_dependency() -> None:
    relationship_dependency_log.append("write")


class DependencyRelationshipController(RelationshipController):
    read_dependencies = (record_relationship_read_dependency,)
    write_dependencies = (record_relationship_write_dependency,)


@pytest.fixture
def relationship_client(minimal_app_factory: Callable[..., FastAPI]) -> Iterator[TestClient]:
    app = minimal_app_factory(RelationshipController(prefix="/api/v1/examples", tags=["examples"]).router)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def nested_relationship_client(minimal_app_factory: Callable[..., FastAPI]) -> Iterator[TestClient]:
    app = minimal_app_factory(NestedRelationshipController(prefix="/nested-examples", tags=["examples"]).router)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _example(session: Session, *, title: str = "관계 예시") -> Example:
    model = Example(title=title, description=None, status=ExampleStatus.DRAFT, score=50)
    ExampleSerializer.initialize_relationship_defaults(model)
    session.add(model)
    session.commit()
    return model


def _identifier(resource_type: str, resource_id: UUID) -> dict[str, str]:
    return {"type": resource_type, "id": str(resource_id)}


def test_to_one_patch_get_linkage_and_get_related_resource(
    relationship_client: TestClient,
    committed_session: Session,
) -> None:
    example = _example(committed_session)
    category = ExampleCategory(name="분류")
    committed_session.add(category)
    committed_session.commit()

    assigned = relationship_client.patch(
        f"/api/v1/examples/{example.id}/relationships/category",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json={"data": _identifier("exampleCategories", category.id)},
    )
    linkage = relationship_client.get(f"/api/v1/examples/{example.id}/relationships/category")
    related = relationship_client.get(f"/api/v1/examples/{example.id}/category")
    cleared = relationship_client.patch(
        f"/api/v1/examples/{example.id}/relationships/category",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json={"data": None},
    )
    cleared_linkage = relationship_client.get(f"/api/v1/examples/{example.id}/relationships/category")

    for response in (assigned, cleared):
        assert response.status_code == 204
        assert response.content == b""
        assert "content-type" not in response.headers
    assert linkage.status_code == 200
    assert linkage.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert linkage.json()["data"] == _identifier("exampleCategories", category.id)
    assert linkage.json()["links"] == {
        "self": f"/api/v1/examples/{example.id}/relationships/category",
        "related": f"/api/v1/examples/{example.id}/category",
    }
    assert related.status_code == 200
    assert related.json()["data"]["type"] == "exampleCategories"
    assert related.json()["data"]["attributes"] == {"name": "분류"}
    assert cleared_linkage.status_code == 200
    assert cleared_linkage.json()["data"] is None

    committed_session.expire_all()
    persisted = committed_session.get(Example, example.id)
    assert persisted is not None
    assert persisted.category_id is None


def test_to_many_post_patch_delete_and_related_collection(
    relationship_client: TestClient,
    committed_session: Session,
) -> None:
    example = _example(committed_session)
    first = ExampleTag(name="첫 태그")
    second = ExampleTag(name="둘째 태그")
    third = ExampleTag(name="셋째 태그")
    committed_session.add_all([first, second, third])
    committed_session.commit()
    path = f"/api/v1/examples/{example.id}/relationships/tags"

    added = relationship_client.post(
        path,
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json={"data": [_identifier("exampleTags", first.id), _identifier("exampleTags", second.id)]},
    )
    replaced = relationship_client.patch(
        path,
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json={"data": [_identifier("exampleTags", second.id), _identifier("exampleTags", third.id)]},
    )
    removed = relationship_client.request(
        "DELETE",
        path,
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json={"data": [_identifier("exampleTags", second.id)]},
    )
    linkage = relationship_client.get(path)
    related = relationship_client.get(f"/api/v1/examples/{example.id}/tags")

    for response in (added, replaced, removed):
        assert response.status_code == 204
        assert response.content == b""
        assert "content-type" not in response.headers
    assert linkage.status_code == 200
    assert linkage.json()["data"] == [_identifier("exampleTags", third.id)]
    assert related.status_code == 200
    assert [item["attributes"]["name"] for item in related.json()["data"]] == ["셋째 태그"]


def test_response_only_relationships_do_not_register_mutation_routes() -> None:
    app = FastAPI()
    app.include_router(ReadOnlyRelationshipController(prefix="/read-only-examples", tags=["examples"]).router)

    paths = app.openapi()["paths"]

    assert set(paths["/read-only-examples/{resource_id}/relationships/category"]) == {"get"}
    assert set(paths["/read-only-examples/{resource_id}/relationships/tags"]) == {"get"}


def test_response_only_relationships_register_only_read_route_names() -> None:
    """Pin the omission at route-name level, not only at HTTP-method level."""

    router = ReadOnlyRelationshipController(prefix="/read-only-examples", tags=["examples"]).router
    relationship_route_names = {
        route.name for route in router.routes if isinstance(route, APIRoute) and ".relationship." in route.name
    }

    assert relationship_route_names == {
        "ReadOnlyRelationshipController.relationship.category.show",
        "ReadOnlyRelationshipController.relationship.category.related",
        "ReadOnlyRelationshipController.relationship.tags.show",
        "ReadOnlyRelationshipController.relationship.tags.related",
    }


def test_relationship_routes_apply_declared_read_and_write_dependencies(
    minimal_app_factory: Callable[..., FastAPI],
    committed_session: Session,
) -> None:
    relationship_dependency_log.clear()
    example = _example(committed_session)
    tag = ExampleTag(name="의존성 태그")
    committed_session.add(tag)
    committed_session.commit()
    app = minimal_app_factory(DependencyRelationshipController(prefix="/dependency-examples", tags=["examples"]).router)
    path = f"/dependency-examples/{example.id}/relationships/tags"
    with TestClient(app, raise_server_exceptions=False) as client:
        read_response = client.get(path)
        write_response = client.post(
            path,
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json={"data": [_identifier("exampleTags", tag.id)]},
        )

    assert read_response.status_code == 200
    assert write_response.status_code == 204
    assert relationship_dependency_log == ["read", "write"]


def test_relationship_mutation_serializes_on_the_parent_row(
    minimal_app_factory: Callable[..., FastAPI],
    concurrent_session_factory: Callable[[], Session],
) -> None:
    with concurrent_session_factory() as setup_session:
        example = _example(setup_session)
        tag = ExampleTag(name="동시 추가")
        setup_session.add(tag)
        setup_session.commit()
        example_id = example.id
        tag_id = tag.id

    app = minimal_app_factory(
        RelationshipController(prefix="/api/v1/examples", tags=["examples"]).router,
        session_factory=concurrent_session_factory,
    )

    with concurrent_session_factory() as lock_session, TestClient(app, raise_server_exceptions=False) as client:
        lock_session.begin()
        locked = lock_session.scalar(select(Example).where(Example.id == example_id).with_for_update(read=True))
        assert locked is not None

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                client.post,
                f"/api/v1/examples/{example_id}/relationships/tags",
                headers={"Content-Type": JSONAPI_MEDIA_TYPE},
                json={"data": [_identifier("exampleTags", tag_id)]},
            )
            blocked_by_parent_lock = False
            try:
                response = future.result(timeout=0.5)
            except FutureTimeoutError:
                blocked_by_parent_lock = True
            finally:
                lock_session.commit()
            response = future.result(timeout=5)

    assert blocked_by_parent_lock
    assert response.status_code == 204


def test_concurrent_adds_of_the_same_relationship_are_idempotent(
    minimal_app_factory: Callable[..., FastAPI],
    concurrent_session_factory: Callable[[], Session],
) -> None:
    with concurrent_session_factory() as setup_session:
        example = _example(setup_session)
        tag = ExampleTag(name="중복 동시 추가")
        setup_session.add(tag)
        setup_session.commit()
        example_id = example.id
        tag_id = tag.id

    app = minimal_app_factory(
        RelationshipController(prefix="/api/v1/examples", tags=["examples"]).router,
        session_factory=concurrent_session_factory,
    )
    barrier = Barrier(2)

    def add_relationship() -> int:
        barrier.wait(timeout=5)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                f"/api/v1/examples/{example_id}/relationships/tags",
                headers={"Content-Type": JSONAPI_MEDIA_TYPE},
                json={"data": [_identifier("exampleTags", tag_id)]},
            )
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _index: add_relationship(), range(2)))

    assert statuses == [204, 204]
    with concurrent_session_factory() as verification_session:
        persisted = verification_session.get(Example, example_id)
        assert persisted is not None
        assert [related.id for related in persisted.tags] == [tag_id]


def test_resource_patch_can_update_relationships_without_attributes(
    relationship_client: TestClient,
    committed_session: Session,
) -> None:
    example = _example(committed_session)
    category = ExampleCategory(name="관계 전용 PATCH")
    committed_session.add(category)
    committed_session.commit()

    response = relationship_client.patch(
        f"/api/v1/examples/{example.id}",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json={
            "data": {
                "type": "examples",
                "id": str(example.id),
                "relationships": {
                    "category": {"data": _identifier("exampleCategories", category.id)},
                },
            }
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["relationships"]["category"]["data"] == _identifier("exampleCategories", category.id)
    committed_session.expire_all()
    persisted = committed_session.get(Example, example.id)
    assert persisted is not None
    assert persisted.category_id == category.id


def test_missing_related_resource_rolls_back_complete_replacement(
    relationship_client: TestClient,
    committed_session: Session,
) -> None:
    example = _example(committed_session)
    existing = ExampleTag(name="유지할 태그")
    replacement = ExampleTag(name="교체 후보")
    committed_session.add_all([existing, replacement])
    example.tags = [existing]
    committed_session.commit()

    response = relationship_client.patch(
        f"/api/v1/examples/{example.id}/relationships/tags",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json={
            "data": [
                _identifier("exampleTags", replacement.id),
                _identifier("exampleTags", uuid4()),
            ]
        },
    )

    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "RELATIONSHIP_RESOURCE_NOT_FOUND"
    assert response.json()["errors"][0]["source"]["pointer"] == "/data/1/id"
    committed_session.expire_all()
    persisted = committed_session.get(Example, example.id)
    assert persisted is not None
    assert [tag.id for tag in persisted.tags] == [existing.id]


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_relationship_mutation_checks_content_type_before_body(
    relationship_client: TestClient,
    committed_session: Session,
    method: str,
) -> None:
    example = _example(committed_session, title=method)

    response = relationship_client.request(
        method,
        f"/api/v1/examples/{example.id}/relationships/tags",
        headers={"Content-Type": "application/json"},
        content="{",
    )

    assert response.status_code == 415
    assert response.json()["errors"][0]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_relationship_routes_reject_type_mismatch_and_unknown_names(
    relationship_client: TestClient,
    committed_session: Session,
) -> None:
    example = _example(committed_session)

    mismatched = relationship_client.patch(
        f"/api/v1/examples/{example.id}/relationships/category",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json={"data": _identifier("exampleTags", uuid4())},
    )
    unknown = relationship_client.get(f"/api/v1/examples/{example.id}/relationships/privateItems")

    assert mismatched.status_code == 409
    assert mismatched.json()["errors"][0]["code"] == "TYPE_MISMATCH"
    assert mismatched.json()["errors"][0]["source"]["pointer"] == "/data/type"
    assert unknown.status_code == 404


def test_resource_writes_assign_declared_relationships(
    relationship_client: TestClient,
    committed_session: Session,
) -> None:
    category = ExampleCategory(name="쓰기 관계")
    committed_session.add(category)
    committed_session.commit()

    response = relationship_client.post(
        "/api/v1/examples",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json={
            "data": {
                "type": "examples",
                "attributes": {
                    "title": "관계 포함 생성",
                    "description": None,
                    "status": "draft",
                    "score": 50,
                },
                "relationships": {
                    "category": {
                        "data": _identifier("exampleCategories", category.id),
                    }
                },
            }
        },
    )

    assert response.status_code == 201
    assert response.json()["data"]["relationships"]["category"]["data"] == _identifier("exampleCategories", category.id)


def _link_query(link: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(link).query))


def test_related_to_one_loads_target_serializer_relationships(
    nested_relationship_client: TestClient,
    committed_session: Session,
) -> None:
    example = _example(committed_session, title="중첩 to-one")
    category = ExampleCategory(name="중첩 분류")
    committed_session.add(category)
    example.category = category
    committed_session.commit()

    response = nested_relationship_client.get(f"/nested-examples/{example.id}/category")

    assert response.status_code == 200
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    body = response.json()
    assert body["data"]["type"] == "exampleCategories"
    assert body["data"]["attributes"] == {"name": "중첩 분류"}
    assert body["data"]["relationships"]["examples"]["data"] == [_identifier("examples", example.id)]


def test_related_to_many_loads_target_serializer_relationships(
    nested_relationship_client: TestClient,
    committed_session: Session,
) -> None:
    example = _example(committed_session, title="중첩 to-many")
    first = ExampleTag(name="중첩 태그 하나")
    second = ExampleTag(name="중첩 태그 둘")
    committed_session.add_all([first, second])
    example.tags = [first, second]
    committed_session.commit()

    response = nested_relationship_client.get(f"/nested-examples/{example.id}/tags")

    assert response.status_code == 200
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    body = response.json()
    assert {item["attributes"]["name"] for item in body["data"]} == {"중첩 태그 하나", "중첩 태그 둘"}
    assert all(
        item["relationships"]["examples"]["data"] == [_identifier("examples", example.id)] for item in body["data"]
    )
    assert body["meta"]["totalCount"] == 2


def test_to_many_related_collection_is_paginated(
    relationship_client: TestClient,
    committed_session: Session,
) -> None:
    example = _example(committed_session, title="페이지네이션 대상")
    tags = [ExampleTag(name=f"페이지 태그 {index}") for index in range(3)]
    committed_session.add_all(tags)
    example.tags = tags
    committed_session.commit()
    ordered_names = [tag.name for tag in sorted(tags, key=lambda tag: tag.id.bytes)]

    first = relationship_client.get(f"/api/v1/examples/{example.id}/tags?page[size]=2")
    second = relationship_client.get(f"/api/v1/examples/{example.id}/tags?page[size]=2&page[number]=2")

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert len(first_body["data"]) == 2
    assert len(second_body["data"]) == 1
    assert first_body["meta"]["totalCount"] == 3
    assert second_body["meta"]["totalCount"] == 3
    assert first_body["links"]["prev"] is None
    assert first_body["links"]["next"] is not None
    assert second_body["links"]["next"] is None
    assert [item["attributes"]["name"] for item in (*first_body["data"], *second_body["data"])] == ordered_names


def test_to_many_related_page_size_is_capped_at_one_hundred(
    relationship_client: TestClient,
    committed_session: Session,
) -> None:
    example = _example(committed_session, title="페이지 상한")
    tag = ExampleTag(name="상한 태그")
    committed_session.add(tag)
    example.tags = [tag]
    committed_session.commit()

    response = relationship_client.get(f"/api/v1/examples/{example.id}/tags?page[size]=1000")

    assert response.status_code == 200
    assert _link_query(response.json()["links"]["self"])["page[size]"] == "100"


@pytest.mark.parametrize(
    ("query", "code", "parameter"),
    [
        ("sort=name", "INVALID_SORT", "sort"),
        ("filter[name]=x", "INVALID_FILTER", "filter[name]"),
        ("include=examples", "INVALID_INCLUDE", "include"),
        ("fields[exampleTags]=name", "INVALID_QUERY_PARAMETER", "fields[exampleTags]"),
        ("unknown=1", "INVALID_QUERY_PARAMETER", "unknown"),
        ("page[size]=0", "INVALID_PAGE", "page[size]"),
        ("page[number]=1&page[number]=2", "INVALID_PAGE", "page[number]"),
    ],
)
def test_to_many_related_rejects_unsupported_query_parameters(
    relationship_client: TestClient,
    committed_session: Session,
    query: str,
    code: str,
    parameter: str,
) -> None:
    example = _example(committed_session, title=f"거부 {parameter}")

    response = relationship_client.get(f"/api/v1/examples/{example.id}/tags?{query}")

    assert response.status_code == 400
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json()["errors"][0]["code"] == code
    assert response.json()["errors"][0]["source"]["parameter"] == parameter


def test_to_one_related_still_rejects_every_query_parameter(
    relationship_client: TestClient,
    committed_session: Session,
) -> None:
    example = _example(committed_session, title="to-one 파라미터 거부")

    response = relationship_client.get(f"/api/v1/examples/{example.id}/category?page[size]=2")

    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "INVALID_QUERY_PARAMETER"
    assert response.json()["errors"][0]["source"]["parameter"] == "page[size]"


def test_related_urls_return_404_for_missing_or_malformed_owner(
    relationship_client: TestClient,
    committed_session: Session,
) -> None:
    missing_id = uuid4()

    missing_collection = relationship_client.get(f"/api/v1/examples/{missing_id}/tags")
    missing_resource = relationship_client.get(f"/api/v1/examples/{missing_id}/category")
    malformed = relationship_client.get("/api/v1/examples/not-a-uuid/tags")

    for response in (missing_collection, missing_resource, malformed):
        assert response.status_code == 404
        assert response.json()["errors"][0]["code"] == "RESOURCE_NOT_FOUND"

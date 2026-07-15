"""Serializer-declared JSON:API relationship action tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.controllers.concerns.crud_actions import CrudActions
from app.jsonapi import JSONAPI_MEDIA_TYPE, register_exception_handlers
from app.models import Example, ExampleCategory, ExampleStatus, ExampleTag
from app.schemas.example import (
    EXAMPLE_QUERY_POLICY,
    ExampleCreate,
    ExampleRelationships,
    ExampleReplace,
    ExampleUpdate,
)
from app.serializers import ExampleSerializer
from config.database import get_session


class RelationshipController(CrudActions[Example, ExampleCreate, ExampleUpdate, ExampleReplace]):
    model_class = Example
    serializer_class = ExampleSerializer
    create_schema = ExampleCreate
    update_schema = ExampleUpdate
    replace_schema = ExampleReplace
    relationships_schema = ExampleRelationships
    query_policy = EXAMPLE_QUERY_POLICY


class ReadOnlyRelationshipController(RelationshipController):
    relationships_schema = None


relationship_dependency_log: list[str] = []


def record_relationship_read_dependency() -> None:
    relationship_dependency_log.append("read")


def record_relationship_write_dependency() -> None:
    relationship_dependency_log.append("write")


class DependencyRelationshipController(RelationshipController):
    read_dependencies = (record_relationship_read_dependency,)
    write_dependencies = (record_relationship_write_dependency,)


@pytest.fixture
def relationship_client(db_engine: Engine) -> Iterator[TestClient]:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(RelationshipController(prefix="/api/v1/examples", tags=["examples"]).router)

    def override_session() -> Iterator[Session]:
        with Session(bind=db_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
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


def test_relationship_routes_apply_declared_read_and_write_dependencies(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    relationship_dependency_log.clear()
    example = _example(committed_session)
    tag = ExampleTag(name="의존성 태그")
    committed_session.add(tag)
    committed_session.commit()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(DependencyRelationshipController(prefix="/dependency-examples", tags=["examples"]).router)

    def override_session() -> Iterator[Session]:
        with Session(bind=db_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
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
    concurrent_session_factory: Callable[[], Session],
) -> None:
    with concurrent_session_factory() as setup_session:
        example = _example(setup_session)
        tag = ExampleTag(name="동시 추가")
        setup_session.add(tag)
        setup_session.commit()
        example_id = example.id
        tag_id = tag.id

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(RelationshipController(prefix="/api/v1/examples", tags=["examples"]).router)

    def override_session() -> Iterator[Session]:
        with concurrent_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session

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
    concurrent_session_factory: Callable[[], Session],
) -> None:
    with concurrent_session_factory() as setup_session:
        example = _example(setup_session)
        tag = ExampleTag(name="중복 동시 추가")
        setup_session.add(tag)
        setup_session.commit()
        example_id = example.id
        tag_id = tag.id

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(RelationshipController(prefix="/api/v1/examples", tags=["examples"]).router)

    def override_session() -> Iterator[Session]:
        with concurrent_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
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

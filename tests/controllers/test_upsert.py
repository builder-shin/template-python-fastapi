"""PostgreSQL-backed JSON:API upsert contract tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from typing import ClassVar
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.controllers.concerns.crud_actions import CrudActions
from app.jsonapi import JSONAPI_MEDIA_TYPE, JsonApiException, register_exception_handlers
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


class UpsertController(CrudActions[Example, ExampleCreate, ExampleUpdate, ExampleReplace]):
    model_class = Example
    serializer_class = ExampleSerializer
    create_schema = ExampleCreate
    update_schema = ExampleUpdate
    replace_schema = ExampleReplace
    relationships_schema = ExampleRelationships
    query_policy = EXAMPLE_QUERY_POLICY
    enable_upsert = True
    hook_log: ClassVar[list[str]] = []

    def before_upsert(self, session: Session, model: Example, attributes: ExampleReplace) -> None:
        del session, model, attributes
        self.hook_log.append("before_upsert")

    def after_upsert(self, session: Session, model: Example, attributes: ExampleReplace) -> None:
        del session, model, attributes
        self.hook_log.append("after_upsert")


class ReadOnlyRelationshipsUpsertController(UpsertController):
    relationships_schema = None


class RollbackUpsertController(UpsertController):
    def after_upsert(self, session: Session, model: Example, attributes: ExampleReplace) -> None:
        super().after_upsert(session, model, attributes)
        raise JsonApiException(status_code=422, code="VALIDATION_ERROR")


class SerializationFailureUpsertController(UpsertController):
    def after_upsert(self, session: Session, model: Example, attributes: ExampleReplace) -> None:
        del attributes
        session.expire(model, ["category"])


class MappedFieldHookController(UpsertController):
    hook_created_at = datetime(2026, 7, 14, 1, 2, 3, tzinfo=UTC)

    def before_upsert(self, session: Session, model: Example, attributes: ExampleReplace) -> None:
        del session, attributes
        model.created_at = self.hook_created_at


class ForeignKeyHookController(UpsertController):
    category_id: ClassVar[UUID | None] = None

    def before_upsert(self, session: Session, model: Example, attributes: ExampleReplace) -> None:
        del session, attributes
        assert self.category_id is not None
        model.category_id = self.category_id


class RelationshipHookController(UpsertController):
    category_id: ClassVar[UUID | None] = None

    def before_upsert(self, session: Session, model: Example, attributes: ExampleReplace) -> None:
        del attributes
        assert self.category_id is not None
        category = session.get(ExampleCategory, self.category_id)
        assert category is not None
        model.category = category


class RelationshipOverrideController(UpsertController):
    assign_calls: ClassVar[int] = 0

    def assign_relationships(
        self,
        session: Session,
        model: Example,
        relationships: BaseModel | None,
    ) -> None:
        type(self).assign_calls += 1
        super().assign_relationships(session, model, relationships)


def _app(controller: UpsertController, session_factory: Callable[[], Session]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(controller.router)

    def override_session() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    return app


def _document(
    resource_id: UUID,
    *,
    title: str,
    status: str = "draft",
    score: int = 10,
    description: str | None | object = None,
    category_id: UUID | None = None,
) -> dict[str, object]:
    attributes: dict[str, object] = {
        "title": title,
        "status": status,
        "score": score,
    }
    if description is not _OMITTED:
        attributes["description"] = description
    data: dict[str, object] = {
        "type": "examples",
        "id": str(resource_id),
        "attributes": attributes,
    }
    if category_id is not None:
        data["relationships"] = {
            "category": {
                "data": {"type": "exampleCategories", "id": str(category_id)},
            }
        }
    return {"data": data}


_OMITTED = object()


def test_put_creates_then_fully_replaces_resource(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    UpsertController.hook_log.clear()
    resource_id = uuid4()
    category = ExampleCategory(name="카테고리")
    committed_session.add(category)
    committed_session.commit()
    app = _app(
        UpsertController(prefix="/api/v1/examples", tags=["examples"]),
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.put(
            f"/api/v1/examples/{resource_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(
                resource_id,
                title="처음",
                description="교체 시 제거",
                category_id=category.id,
            ),
        )
        replaced = client.put(
            f"/api/v1/examples/{resource_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(
                resource_id,
                title="교체",
                status="active",
                score=90,
                description=_OMITTED,
            ),
        )

    assert created.status_code == 201
    assert created.headers["location"] == f"/api/v1/examples/{resource_id}"
    assert replaced.status_code == 200
    assert replaced.json()["data"]["attributes"] == {
        "title": "교체",
        "description": None,
        "status": "active",
        "score": 90,
        "createdAt": created.json()["data"]["attributes"]["createdAt"],
        "updatedAt": replaced.json()["data"]["attributes"]["updatedAt"],
    }
    assert replaced.json()["data"]["relationships"]["category"]["data"] is None
    assert UpsertController.hook_log == [
        "before_upsert",
        "after_upsert",
        "before_upsert",
        "after_upsert",
    ]

    committed_session.expire_all()
    persisted = committed_session.get(Example, resource_id)
    assert persisted is not None
    assert persisted.description is None
    assert persisted.category_id is None


def test_put_create_rolls_back_when_response_serialization_fails(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    resource_id = uuid4()
    app = _app(
        SerializationFailureUpsertController(prefix="/serialization-examples", tags=["examples"]),
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.put(
            f"/serialization-examples/{resource_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(resource_id, title="롤백 대상"),
        )

    assert response.status_code == 500
    assert committed_session.get(Example, resource_id) is None


def test_put_create_location_matches_canonical_resource_link(db_engine: Engine) -> None:
    resource_id = uuid4()
    uppercase_id = str(resource_id).upper()
    document = _document(resource_id, title="정규 URL")
    data = document["data"]
    assert isinstance(data, dict)
    data["id"] = uppercase_id
    app = _app(
        UpsertController(prefix="/api/v1/examples", tags=["examples"]),
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.put(
            f"/api/v1/examples/{uppercase_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=document,
        )

    assert response.status_code == 201
    assert response.headers["location"] == response.json()["data"]["links"]["self"]


def test_put_preserves_relationships_excluded_from_the_write_schema(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    tag = ExampleTag(name="응답 전용 태그")
    model = Example(
        title="교체 전",
        description=None,
        status=ExampleStatus.DRAFT,
        score=10,
    )
    ExampleSerializer.initialize_relationship_defaults(model)
    model.tags = [tag]
    committed_session.add(model)
    committed_session.commit()
    app = _app(
        ReadOnlyRelationshipsUpsertController(prefix="/api/v1/examples", tags=["examples"]),
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.put(
            f"/api/v1/examples/{model.id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(model.id, title="교체 후"),
        )

    assert response.status_code == 200
    assert response.json()["data"]["relationships"]["tags"]["data"] == [{"type": "exampleTags", "id": str(tag.id)}]
    committed_session.expire_all()
    persisted = committed_session.get(Example, model.id)
    assert persisted is not None
    assert [related.id for related in persisted.tags] == [tag.id]


def test_put_requires_complete_replacement_attributes(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    resource_id = uuid4()
    app = _app(
        UpsertController(prefix="/api/v1/examples", tags=["examples"]),
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.put(
            f"/api/v1/examples/{resource_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json={
                "data": {
                    "type": "examples",
                    "id": str(resource_id),
                    "attributes": {"title": "불완전"},
                }
            },
        )

    assert response.status_code == 422
    assert committed_session.scalar(select(func.count()).select_from(Example)) == 0


def test_upsert_hooks_share_the_write_transaction(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    resource_id = uuid4()
    app = _app(
        RollbackUpsertController(prefix="/rollback-examples", tags=["examples"]),
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.put(
            f"/rollback-examples/{resource_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(resource_id, title="롤백"),
        )

    assert response.status_code == 422
    assert committed_session.scalar(select(func.count()).select_from(Example)) == 0


def test_before_upsert_persists_mapped_fields_outside_public_params(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    resource_id = uuid4()
    app = _app(
        MappedFieldHookController(prefix="/hook-examples", tags=["examples"]),
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.put(
            f"/hook-examples/{resource_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(resource_id, title="훅 필드"),
        )

    assert response.status_code == 201
    assert response.json()["data"]["attributes"]["createdAt"] == "2026-07-14T01:02:03+00:00"
    committed_session.expire_all()
    persisted = committed_session.get(Example, resource_id)
    assert persisted is not None
    assert persisted.created_at == MappedFieldHookController.hook_created_at


def test_before_upsert_foreign_key_is_not_overwritten_by_omitted_relationship(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    category = ExampleCategory(name="훅 관계")
    committed_session.add(category)
    committed_session.commit()
    ForeignKeyHookController.category_id = category.id
    resource_id = uuid4()
    app = _app(
        ForeignKeyHookController(prefix="/foreign-key-hook-examples", tags=["examples"]),
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.put(
            f"/foreign-key-hook-examples/{resource_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(resource_id, title="FK 훅"),
        )

    assert response.status_code == 201
    assert response.json()["data"]["relationships"]["category"]["data"] == {
        "type": "exampleCategories",
        "id": str(category.id),
    }
    committed_session.expire_all()
    persisted = committed_session.get(Example, resource_id)
    assert persisted is not None
    assert persisted.category_id == category.id


def test_before_upsert_relationship_assignment_is_applied_after_atomic_insert(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    category = ExampleCategory(name="관계 객체 훅")
    committed_session.add(category)
    committed_session.commit()
    RelationshipHookController.category_id = category.id
    resource_id = uuid4()
    app = _app(
        RelationshipHookController(prefix="/relationship-hook-examples", tags=["examples"]),
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.put(
            f"/relationship-hook-examples/{resource_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(resource_id, title="관계 객체 훅"),
        )

    assert response.status_code == 201
    assert response.json()["data"]["relationships"]["category"]["data"] == {
        "type": "exampleCategories",
        "id": str(category.id),
    }
    committed_session.expire_all()
    persisted = committed_session.get(Example, resource_id)
    assert persisted is not None
    assert persisted.category_id == category.id


def test_create_and_replace_upsert_use_public_relationship_extension_point(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    del committed_session
    RelationshipOverrideController.assign_calls = 0
    resource_id = uuid4()
    controller = RelationshipOverrideController(prefix="/override-examples", tags=["examples"])
    app = _app(
        controller,
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.put(
            f"/override-examples/{resource_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(resource_id, title="생성"),
        )
        replaced = client.put(
            f"/override-examples/{resource_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(resource_id, title="교체"),
        )

    assert created.status_code == 201
    assert replaced.status_code == 200
    assert RelationshipOverrideController.assign_calls == 2


def test_upsert_openapi_declares_both_success_statuses(db_engine: Engine) -> None:
    app = _app(
        UpsertController(prefix="/api/v1/examples", tags=["examples"]),
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )

    operation = app.openapi()["paths"]["/api/v1/examples/{resource_id}"]["put"]

    assert set(operation["responses"]).issuperset({"200", "201"})
    for status in ("200", "201"):
        content = operation["responses"][status]["content"]
        assert set(content) == {JSONAPI_MEDIA_TYPE}
        assert content[JSONAPI_MEDIA_TYPE]["schema"]["$ref"].endswith("/SuccessDocument")
    assert "Location" in operation["responses"]["201"]["headers"]


def test_concurrent_puts_to_the_same_uuid_leave_one_resource(
    concurrent_session_factory: Callable[[], Session],
) -> None:
    resource_id = uuid4()
    app = _app(
        UpsertController(prefix="/api/v1/examples", tags=["examples"]),
        concurrent_session_factory,
    )
    barrier = Barrier(2)

    def put(title: str) -> tuple[int, str]:
        barrier.wait(timeout=5)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                f"/api/v1/examples/{resource_id}",
                headers={"Content-Type": JSONAPI_MEDIA_TYPE},
                json=_document(resource_id, title=title),
            )
        return response.status_code, response.json()["data"]["attributes"]["title"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(put, ("첫 번째", "두 번째")))

    assert sorted(status for status, _title in results) == [200, 201]
    with concurrent_session_factory() as verification_session:
        assert verification_session.scalar(select(func.count()).select_from(Example)) == 1
        persisted = verification_session.get(Example, resource_id)
        assert persisted is not None
        assert persisted.title in {"첫 번째", "두 번째"}

"""PostgreSQL-backed JSON:API upsert contract tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Barrier
from typing import ClassVar
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Engine, ForeignKey, String, event, func, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from app.controllers.concerns.crud_actions import CrudActions
from app.controllers.concerns.jsonapi_controller import JsonApiController
from app.jsonapi import JSONAPI_MEDIA_TYPE, JsonApiException, register_exception_handlers
from app.jsonapi.naming import JsonApiWriteSchema
from app.jsonapi.query import QueryPolicy, SortTerm
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
from config.database import get_session


class UpsertController(CrudActions[Example, ExampleCreate, ExampleUpdate, ExampleReplace]):
    model_class = Example
    serializer_class = ExampleSerializer
    create_schema = ExampleCreate
    update_schema = ExampleUpdate
    replace_schema = ExampleReplace
    relationships_schema: type[BaseModel] | None = ExampleRelationships
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
    """Force a serializer failure inside the upsert transaction.

    ``tags`` is expired because it has no ``linkage_attribute``: its linkage cannot be
    derived from a local foreign key, so an unloaded ``tags`` is still the one relationship
    that makes serialization fail after the hooks ran.
    """

    def after_upsert(self, session: Session, model: Example, attributes: ExampleReplace) -> None:
        del attributes
        session.expire(model, ["tags"])


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


def _app(controller: JsonApiController, session_factory: Callable[[], Session]) -> FastAPI:
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
    tag_ids: Sequence[UUID] = (),
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
    relationships: dict[str, object] = {}
    if category_id is not None:
        relationships["category"] = {
            "data": {"type": "exampleCategories", "id": str(category_id)},
        }
    if tag_ids:
        relationships["tags"] = {
            "data": [{"type": "exampleTags", "id": str(tag_id)} for tag_id in tag_ids],
        }
    if relationships:
        data["relationships"] = relationships
    return {"data": data}


_OMITTED = object()


@contextmanager
def _recorded_statements(engine: Engine) -> Iterator[list[str]]:
    """Collect every SQL statement the engine executes while the block runs.

    The listener is always removed again so a leaked one cannot pollute the statement
    counts of the next test.
    """

    statements: list[str] = []

    def record(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, context, executemany
        statements.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _addressed_row_reads(statements: Sequence[str]) -> list[str]:
    """Return every full read of the addressed example row.

    ``PUT`` used to read that row twice: once to decide 201 vs 200 and once more after the
    atomic statement. The post-flush ``updated_at`` refresh selects a single column, so it
    is deliberately not a full read and is not counted here.
    """

    return [
        statement
        for statement in statements
        if statement.startswith("SELECT ")
        and " FROM examples WHERE examples.id = " in statement
        and "examples.title" in statement
    ]


def _reverse_collection_reads(statements: Sequence[str]) -> list[str]:
    """Return reads that walk a relationship target's reverse collection.

    Their cost grows with the target's collection, not with the request, so they are pinned
    semantically rather than by a count.
    """

    return [
        statement
        for statement in statements
        if statement.startswith("SELECT ")
        and ("= examples.category_id" in statement or "example_tags.tag_id" in statement)
    ]


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


def test_put_create_reads_the_resource_once(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    del committed_session
    resource_id = uuid4()
    app = _app(
        UpsertController(prefix="/api/v1/examples", tags=["examples"]),
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )

    with TestClient(app, raise_server_exceptions=False) as client, _recorded_statements(db_engine) as statements:
        response = client.put(
            f"/api/v1/examples/{resource_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(resource_id, title="한 번만 조회"),
        )

    assert response.status_code == 201
    assert len(_addressed_row_reads(statements)) == 1
    # pg_advisory_xact_lock, SELECT examples, INSERT ... ON CONFLICT ... RETURNING
    assert len(statements) == 3, statements
    assert statements[0].startswith("SELECT pg_advisory_xact_lock")
    assert statements[2].startswith("INSERT INTO examples")
    assert "ON CONFLICT" in statements[2]


def test_put_create_with_relationships_reads_the_resource_once(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    category = ExampleCategory(name="관계 카운트")
    tag = ExampleTag(name="관계 카운트 태그")
    committed_session.add_all([category, tag])
    committed_session.commit()
    resource_id = uuid4()
    app = _app(
        UpsertController(prefix="/api/v1/examples", tags=["examples"]),
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )

    with TestClient(app, raise_server_exceptions=False) as client, _recorded_statements(db_engine) as statements:
        response = client.put(
            f"/api/v1/examples/{resource_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(resource_id, title="관계 포함", category_id=category.id, tag_ids=[tag.id]),
        )

    assert response.status_code == 201
    relationships = response.json()["data"]["relationships"]
    assert relationships["category"]["data"] == {"type": "exampleCategories", "id": str(category.id)}
    assert relationships["tags"]["data"] == [{"type": "exampleTags", "id": str(tag.id)}]
    assert len(_addressed_row_reads(statements)) == 1
    # pg_advisory_xact_lock, SELECT examples, SELECT categories, SELECT tags,
    # INSERT ... ON CONFLICT ... RETURNING, UPDATE examples SET category_id,
    # INSERT INTO example_tags, SELECT examples.updated_at
    assert len(statements) == 8, statements

    committed_session.expire_all()
    persisted = committed_session.get(Example, resource_id)
    assert persisted is not None
    assert persisted.category_id == category.id
    assert [related.id for related in persisted.tags] == [tag.id]


def test_put_create_does_not_load_reverse_relationship_collections(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    category = ExampleCategory(name="기존 소유 카테고리")
    tag = ExampleTag(name="기존 소유 태그")
    owned = Example(
        title="이미 존재",
        description=None,
        status=ExampleStatus.DRAFT,
        score=10,
        category=category,
    )
    ExampleSerializer.initialize_relationship_defaults(owned)
    owned.tags = [tag]
    committed_session.add(owned)
    committed_session.commit()
    resource_id = uuid4()
    app = _app(
        UpsertController(prefix="/api/v1/examples", tags=["examples"]),
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )

    with TestClient(app, raise_server_exceptions=False) as client, _recorded_statements(db_engine) as statements:
        response = client.put(
            f"/api/v1/examples/{resource_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(resource_id, title="역방향 미적재", category_id=category.id, tag_ids=[tag.id]),
        )

    assert response.status_code == 201
    assert _reverse_collection_reads(statements) == []

    committed_session.expire_all()
    persisted = committed_session.get(Example, resource_id)
    assert persisted is not None
    assert persisted.category_id == category.id
    assert [related.id for related in persisted.tags] == [tag.id]


def test_put_replace_statement_count_is_unchanged(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    del committed_session
    resource_id = uuid4()
    app = _app(
        UpsertController(prefix="/api/v1/examples", tags=["examples"]),
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.put(
            f"/api/v1/examples/{resource_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(resource_id, title="교체 전"),
        )
        with _recorded_statements(db_engine) as statements:
            replaced = client.put(
                f"/api/v1/examples/{resource_id}",
                headers={"Content-Type": JSONAPI_MEDIA_TYPE},
                json=_document(resource_id, title="교체 후"),
            )

    assert created.status_code == 201
    assert replaced.status_code == 200
    assert len(_addressed_row_reads(statements)) == 1
    # pg_advisory_xact_lock, SELECT examples, selectin tags, INSERT ... ON CONFLICT.
    # The replace path is deliberately untouched: its two loads are what serialize the
    # response relationships, not a duplicate read.
    assert len(statements) == 4, statements


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

    with TestClient(app, raise_server_exceptions=False) as client, _recorded_statements(db_engine) as statements:
        response = client.put(
            f"/foreign-key-hook-examples/{resource_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(resource_id, title="FK 훅"),
        )

    assert response.status_code == 201
    assert len(_addressed_row_reads(statements)) == 1
    # pg_advisory_xact_lock, SELECT examples, INSERT ... ON CONFLICT ... RETURNING,
    # SELECT categories to materialize the linkage the hook wrote as a bare foreign key
    assert len(statements) == 4, statements
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


class ConcurrentlyCreatedUpsertController(UpsertController):
    """Let another transaction commit the addressed row after the pre-check decided.

    ``before_upsert`` runs inside the write transaction, after ``created`` was fixed by
    the pre-check ``SELECT`` and before the ``INSERT ... ON CONFLICT DO UPDATE`` runs, so
    committing the row here is exactly the window the advisory lock cannot close: a plain
    insert from another connection never takes that lock.
    """

    rival_engine: ClassVar[Engine | None] = None
    rival_tag_id: ClassVar[UUID | None] = None

    def before_upsert(self, session: Session, model: Example, attributes: ExampleReplace) -> None:
        super().before_upsert(session, model, attributes)
        engine = type(self).rival_engine
        tag_id = type(self).rival_tag_id
        assert engine is not None
        assert tag_id is not None
        with Session(bind=engine, expire_on_commit=False) as rival:
            tag = rival.get(ExampleTag, tag_id)
            assert tag is not None
            rival_model = Example(
                id=model.id,
                title="다른 트랜잭션",
                description=None,
                status=ExampleStatus.DRAFT,
                score=1,
            )
            ExampleSerializer.initialize_relationship_defaults(rival_model)
            rival_model.tags = [tag]
            rival.add(rival_model)
            rival.commit()


def test_put_create_reads_back_when_postgresql_took_the_conflict_update_branch(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    """``created`` comes from a pre-check; only ``xmax`` says which branch really ran.

    Trusting the pre-check let the in-place fast path publish a committed 201 document
    whose ``tags`` linkage was empty while the row it described already carried a tag.
    """

    tag = ExampleTag(name="경쟁 태그")
    committed_session.add(tag)
    committed_session.commit()
    tag_id = tag.id
    resource_id = uuid4()
    ConcurrentlyCreatedUpsertController.rival_engine = db_engine
    ConcurrentlyCreatedUpsertController.rival_tag_id = tag_id
    app = _app(
        ConcurrentlyCreatedUpsertController(prefix="/api/v1/examples", tags=["examples"]),
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )

    try:
        with TestClient(app, raise_server_exceptions=False) as client, _recorded_statements(db_engine) as statements:
            response = client.put(
                f"/api/v1/examples/{resource_id}",
                headers={"Content-Type": JSONAPI_MEDIA_TYPE},
                json=_document(resource_id, title="이 요청"),
            )
    finally:
        ConcurrentlyCreatedUpsertController.rival_engine = None
        ConcurrentlyCreatedUpsertController.rival_tag_id = None

    assert response.status_code == 201
    body = response.json()["data"]
    assert body["attributes"]["title"] == "이 요청"
    assert body["relationships"]["tags"]["data"] == [{"type": "exampleTags", "id": str(tag_id)}]
    # A stale "create" verdict falls back to the read-back path, unlike a real create.
    assert len(_addressed_row_reads(statements)) == 2

    committed_session.expire_all()
    persisted = committed_session.get(Example, resource_id)
    assert persisted is not None
    assert persisted.title == "이 요청"
    assert [related.id for related in persisted.tags] == [tag_id]


class _RemoteFkBase(DeclarativeBase):
    """Declarative base for a to-one whose foreign key lives on the remote side.

    No application model has that shape, and it is the shape that breaks when a created
    to-one is resolved as ``session.get(target, <local column>)``: the local column is the
    owner primary key, so the lookup fetches whichever target happens to share its value.
    """


class _RemoteFkOwner(_RemoteFkBase):
    __tablename__ = "upsert_remote_fk_owners"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    profile: Mapped[_RemoteFkProfile | None] = relationship(back_populates="owner", uselist=False)


class _RemoteFkProfile(_RemoteFkBase):
    __tablename__ = "upsert_remote_fk_profiles"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("upsert_remote_fk_owners.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner: Mapped[_RemoteFkOwner] = relationship(back_populates="profile")


class _RemoteFkProfileSerializer(JsonApiSerializer[_RemoteFkProfile]):
    type_name = "upsertRemoteFkProfiles"
    resource_path = None
    attributes = ()


class _RemoteFkOwnerSerializer(JsonApiSerializer[_RemoteFkOwner]):
    type_name = "upsertRemoteFkOwners"
    resource_path = "/remote-fk-owners"
    attributes = ("title",)
    relationships: ClassVar[dict[str, RelationshipDefinition]] = {
        "profile": RelationshipDefinition(
            attribute="profile",
            serializer=_RemoteFkProfileSerializer,
            many=False,
        )
    }


class _RemoteFkOwnerWrite(JsonApiWriteSchema):
    title: str


class RemoteFkUpsertController(
    CrudActions[_RemoteFkOwner, _RemoteFkOwnerWrite, _RemoteFkOwnerWrite, _RemoteFkOwnerWrite]
):
    model_class = _RemoteFkOwner
    serializer_class = _RemoteFkOwnerSerializer
    create_schema = _RemoteFkOwnerWrite
    update_schema = _RemoteFkOwnerWrite
    replace_schema = _RemoteFkOwnerWrite
    relationships_schema: type[BaseModel] | None = None
    query_policy = QueryPolicy(
        filters={},
        sorts={"title": _RemoteFkOwner.title},
        includes=frozenset({"profile"}),
        default_sort=(SortTerm("title"),),
        tie_breaker=SortTerm("id", column=_RemoteFkOwner.id),
    )
    enable_upsert = True


@pytest.fixture
def remote_fk_tables(db_engine: Engine) -> Iterator[None]:
    _RemoteFkBase.metadata.create_all(db_engine)
    try:
        yield
    finally:
        _RemoteFkBase.metadata.drop_all(db_engine)


def test_put_create_does_not_borrow_a_to_one_whose_foreign_key_lives_on_the_remote_side(
    db_engine: Engine,
    remote_fk_tables: None,
) -> None:
    """A created to-one must be resolved through the relationship join, not a bare get.

    Reading ``RETURNING[<local column>]`` as a foreign key into the target primary key
    made the 201 document advertise linkage to a profile owned by a different resource.
    """

    del remote_fk_tables
    resource_id = uuid4()
    with Session(bind=db_engine, expire_on_commit=False) as setup:
        other_owner = _RemoteFkOwner(title="다른 소유자")
        setup.add(other_owner)
        setup.flush()
        other_owner_id = other_owner.id
        setup.add(_RemoteFkProfile(id=resource_id, owner_id=other_owner_id))
        setup.commit()

    app = _app(
        RemoteFkUpsertController(prefix="/remote-fk-owners", tags=["remote-fk-owners"]),
        lambda: Session(bind=db_engine, expire_on_commit=False),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.put(
            f"/remote-fk-owners/{resource_id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json={
                "data": {
                    "type": "upsertRemoteFkOwners",
                    "id": str(resource_id),
                    "attributes": {"title": "새 소유자"},
                }
            },
        )

    assert response.status_code == 201
    assert response.json()["data"]["relationships"]["profile"]["data"] is None
    with Session(bind=db_engine, expire_on_commit=False) as verification:
        borrowed = verification.get(_RemoteFkProfile, resource_id)
        assert borrowed is not None
        assert borrowed.owner_id == other_owner_id

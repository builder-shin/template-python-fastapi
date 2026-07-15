"""Rails-style inherited CRUD action contract tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any, ClassVar
from uuid import uuid4

import pytest
from fastapi import Body, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, Select, func, select
from sqlalchemy.orm import Session
from starlette.datastructures import QueryParams
from starlette.responses import Response

from app.controllers.concerns.crud_actions import CrudActions
from app.jsonapi import JSONAPI_MEDIA_TYPE, JsonApiException, ResourceIdentifier, register_exception_handlers
from app.jsonapi.query import parse_query
from app.models import Example, ExampleStatus
from app.schemas.example import (
    EXAMPLE_QUERY_POLICY,
    ExampleCreate,
    ExampleRelationships,
    ExampleReplace,
    ExampleUpdate,
)
from app.serializers import ExampleSerializer
from config.database import get_session


class ExampleCrudController(CrudActions[Example, ExampleCreate, ExampleUpdate, ExampleReplace]):
    model_class = Example
    serializer_class = ExampleSerializer
    create_schema = ExampleCreate
    update_schema = ExampleUpdate
    replace_schema = ExampleReplace
    relationships_schema = ExampleRelationships
    query_policy = EXAMPLE_QUERY_POLICY
    hook_log: ClassVar[list[str]] = []

    def before_create(self, session: Session, model: Example, attributes: ExampleCreate) -> None:
        del session, model, attributes
        self.hook_log.append("before_create")

    def after_create(self, session: Session, model: Example, attributes: ExampleCreate) -> None:
        del session, model, attributes
        self.hook_log.append("after_create")

    def before_update(self, session: Session, model: Example, attributes: ExampleUpdate) -> None:
        del session, model, attributes
        self.hook_log.append("before_update")

    def after_update(self, session: Session, model: Example, attributes: ExampleUpdate) -> None:
        del session, model, attributes
        self.hook_log.append("after_update")

    def before_destroy(self, session: Session, model: Example) -> None:
        del session, model
        self.hook_log.append("before_destroy")

    def after_destroy(self, session: Session, model: Example) -> None:
        del session, model
        self.hook_log.append("after_destroy")


class RollbackController(ExampleCrudController):
    def after_create(self, session: Session, model: Example, attributes: ExampleCreate) -> None:
        super().after_create(session, model, attributes)
        raise JsonApiException(status_code=422, code="VALIDATION_ERROR")


class CreateSerializationFailureController(ExampleCrudController):
    def after_create(self, session: Session, model: Example, attributes: ExampleCreate) -> None:
        del attributes
        session.expire(model, ["category"])


class UpdateSerializationFailureController(ExampleCrudController):
    def after_update(self, session: Session, model: Example, attributes: ExampleUpdate) -> None:
        del attributes
        session.expire(model, ["category"])


class ScopedCrudController(ExampleCrudController):
    def index_scope(self, statement: Select[Any]) -> Select[Any]:
        return statement.where(Example.status == ExampleStatus.ACTIVE)

    def model_params(
        self,
        attributes: ExampleCreate | ExampleUpdate | ExampleReplace,
        *,
        exclude_unset: bool,
    ) -> dict[str, object]:
        values = dict(super().model_params(attributes, exclude_unset=exclude_unset))
        title = values.get("title")
        if isinstance(title, str):
            values["title"] = title.upper()
        return values


dependency_log: list[str] = []


def record_read_dependency() -> None:
    dependency_log.append("read")


def record_write_dependency() -> None:
    dependency_log.append("write")


class DependencyCrudController(ExampleCrudController):
    read_dependencies = (record_read_dependency,)
    write_dependencies = (record_write_dependency,)


class DeleteRelationshipDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[ResourceIdentifier]


class BodyDeleteController(ExampleCrudController):
    def __init__(self, *, prefix: str, tags: list[str]) -> None:
        super().__init__(prefix=prefix, tags=tags)
        self.router.add_api_route(
            "/{resource_id}/relationships/tags",
            self.delete_relationships,
            methods=["DELETE"],
            status_code=204,
            response_class=Response,
        )

    def delete_relationships(
        self,
        resource_id: str,
        document: Annotated[DeleteRelationshipDocument, Body(media_type=JSONAPI_MEDIA_TYPE)],
    ) -> Response:
        del resource_id, document
        return Response(status_code=204)


@pytest.fixture
def crud_app(db_engine: Engine) -> Iterator[FastAPI]:
    ExampleCrudController.hook_log.clear()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(ExampleCrudController(prefix="/api/v1/examples", tags=["examples"]).router)

    def override_session() -> Iterator[Session]:
        with Session(bind=db_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    yield app


@pytest.fixture
def crud_client(crud_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(crud_app, raise_server_exceptions=False) as client:
        yield client


def _document(
    *,
    title: str = "생성",
    status: str = "draft",
    score: int = 50,
    description: str | None = None,
) -> dict[str, object]:
    return {
        "data": {
            "type": "examples",
            "attributes": {
                "title": title,
                "description": description,
                "status": status,
                "score": score,
            },
        }
    }


def _create_example(session: Session, *, title: str, score: int = 50) -> Example:
    model = Example(title=title, description=None, status=ExampleStatus.DRAFT, score=score)
    ExampleSerializer.initialize_relationship_defaults(model)
    session.add(model)
    session.commit()
    return model


def test_create_runs_hooks_inside_transaction(crud_client: TestClient) -> None:
    response = crud_client.post(
        "/api/v1/examples",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json=_document(),
    )

    assert response.status_code == 201
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.headers["location"] == f"/api/v1/examples/{response.json()['data']['id']}"
    assert response.json()["data"]["attributes"]["title"] == "생성"
    assert ExampleCrudController.hook_log == ["before_create", "after_create"]


def test_create_rolls_back_when_after_hook_raises(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(RollbackController(prefix="/rollback-examples", tags=["examples"]).router)

    def override_session() -> Iterator[Session]:
        with Session(bind=db_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/rollback-examples",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(),
        )

    assert response.status_code == 422
    assert committed_session.scalar(select(func.count()).select_from(Example)) == 0


def test_create_rolls_back_when_response_serialization_fails(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(CreateSerializationFailureController(prefix="/serialization-examples", tags=["examples"]).router)

    def override_session() -> Iterator[Session]:
        with Session(bind=db_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/serialization-examples",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(),
        )

    assert response.status_code == 500
    assert committed_session.scalar(select(func.count()).select_from(Example)) == 0


def test_update_rolls_back_when_response_serialization_fails(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    model = _create_example(committed_session, title="변경 전")
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(UpdateSerializationFailureController(prefix="/serialization-examples", tags=["examples"]).router)

    def override_session() -> Iterator[Session]:
        with Session(bind=db_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.patch(
            f"/serialization-examples/{model.id}",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json={
                "data": {
                    "type": "examples",
                    "id": str(model.id),
                    "attributes": {"title": "변경 후"},
                }
            },
        )

    assert response.status_code == 500
    committed_session.expire_all()
    persisted = committed_session.get(Example, model.id)
    assert persisted is not None
    assert persisted.title == "변경 전"


def test_content_type_is_rejected_before_malformed_body(crud_client: TestClient) -> None:
    response = crud_client.post(
        "/api/v1/examples",
        headers={"Content-Type": "application/json"},
        content="{",
    )

    assert response.status_code == 415
    assert response.json()["errors"][0]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_body_bearing_delete_rejects_content_type_before_deserialization(db_engine: Engine) -> None:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(BodyDeleteController(prefix="/api/v1/examples", tags=["examples"]).router)

    def override_session() -> Iterator[Session]:
        with Session(bind=db_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.request(
            "DELETE",
            f"/api/v1/examples/{uuid4()}/relationships/tags",
            headers={"Content-Type": "application/json"},
            content="{",
        )

    assert response.status_code == 415
    assert response.json()["errors"][0]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_create_document_forbids_unknown_members(crud_client: TestClient) -> None:
    document = _document()
    data = document["data"]
    assert isinstance(data, dict)
    attributes = data["attributes"]
    assert isinstance(attributes, dict)
    attributes["privateValue"] = "hidden"

    response = crud_client.post(
        "/api/v1/examples",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json=document,
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["source"]["pointer"].endswith("/attributes/privateValue")


def test_create_rejects_unsupported_client_generated_id_with_403(
    crud_client: TestClient,
) -> None:
    document = _document()
    data = document["data"]
    assert isinstance(data, dict)
    data["id"] = str(uuid4())

    response = crud_client.post(
        "/api/v1/examples",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json=document,
    )

    assert response.status_code == 403
    error = response.json()["errors"][0]
    assert error["code"] == "CLIENT_GENERATED_ID_UNSUPPORTED"
    assert error["source"]["pointer"] == "/data/id"


def test_create_rejects_missing_relationship_resource(
    crud_client: TestClient,
    committed_session: Session,
) -> None:
    document = _document()
    data = document["data"]
    assert isinstance(data, dict)
    data["relationships"] = {"category": {"data": {"type": "exampleCategories", "id": str(uuid4())}}}

    response = crud_client.post(
        "/api/v1/examples",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json=document,
    )

    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "RELATIONSHIP_RESOURCE_NOT_FOUND"
    assert response.json()["errors"][0]["source"]["pointer"] == "/data/relationships/category/data/id"
    assert committed_session.scalar(select(func.count()).select_from(Example)) == 0


def test_missing_update_relationship_rolls_back_attributes(
    crud_client: TestClient,
    committed_session: Session,
) -> None:
    model = _create_example(committed_session, title="before")
    resource_id = model.id

    response = crud_client.patch(
        f"/api/v1/examples/{resource_id}",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json={
            "data": {
                "type": "examples",
                "id": str(resource_id),
                "attributes": {"title": "after"},
                "relationships": {"category": {"data": {"type": "exampleCategories", "id": str(uuid4())}}},
            }
        },
    )

    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "RELATIONSHIP_RESOURCE_NOT_FOUND"
    committed_session.expire_all()
    persisted = committed_session.get(Example, resource_id)
    assert persisted is not None
    assert persisted.title == "before"


def test_write_actions_reject_unsupported_query_parameters(
    crud_client: TestClient,
    committed_session: Session,
) -> None:
    created = crud_client.post(
        "/api/v1/examples?fields[examples]=title",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json=_document(),
    )
    model = _create_example(committed_session, title="unchanged")
    resource_id = model.id
    updated = crud_client.patch(
        f"/api/v1/examples/{resource_id}?fields[examples]=title",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json={
            "data": {
                "type": "examples",
                "id": str(resource_id),
                "attributes": {"title": "changed"},
            }
        },
    )
    destroyed = crud_client.delete(
        f"/api/v1/examples/{resource_id}?fields[examples]=title",
    )

    for response in (created, updated, destroyed):
        assert response.status_code == 400
        assert response.json()["errors"][0]["code"] == "INVALID_QUERY_PARAMETER"
        assert response.json()["errors"][0]["source"]["parameter"] == "fields[examples]"

    committed_session.expire_all()
    persisted = committed_session.get(Example, resource_id)
    assert persisted is not None
    assert persisted.title == "unchanged"


def test_index_applies_filter_sort_page_and_returns_links(
    crud_client: TestClient,
    committed_session: Session,
) -> None:
    _create_example(committed_session, title="beta", score=20)
    _create_example(committed_session, title="alpha", score=80)
    _create_example(committed_session, title="alpine", score=40)

    response = crud_client.get(
        "/api/v1/examples?filter[title][contains]=alp&sort=-score&page[number]=1&page[size]=1",
        headers={"Host": "evil.example"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [resource["attributes"]["title"] for resource in body["data"]] == ["alpha"]
    assert body["meta"] == {"totalCount": 2}
    assert body["links"]["next"].endswith("page%5Bnumber%5D=2&page%5Bsize%5D=1")
    assert body["links"]["next"].startswith("/api/v1/examples?")
    assert "evil.example" not in str(body["links"])


def test_empty_include_returns_an_empty_compound_document_member(
    crud_client: TestClient,
    committed_session: Session,
) -> None:
    model = _create_example(committed_session, title="포함 없음")

    indexed = crud_client.get("/api/v1/examples?include=")
    shown = crud_client.get(f"/api/v1/examples/{model.id}?include=")

    assert indexed.status_code == 200
    assert indexed.json()["included"] == []
    assert shown.status_code == 200
    assert shown.json()["included"] == []


@pytest.mark.parametrize(
    ("query", "code", "parameter"),
    [
        ("filter[title]=ignored", "INVALID_FILTER", "filter[title]"),
        ("sort=title", "INVALID_SORT", "sort"),
        ("page[number]=1", "INVALID_PAGE", "page[number]"),
    ],
)
def test_show_rejects_collection_only_query_parameters(
    crud_client: TestClient,
    committed_session: Session,
    query: str,
    code: str,
    parameter: str,
) -> None:
    model = _create_example(committed_session, title="단건")

    response = crud_client.get(f"/api/v1/examples/{model.id}?{query}")

    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == code
    assert response.json()["errors"][0]["source"]["parameter"] == parameter


def test_index_scope_and_model_params_are_inherited_extension_points(
    db_engine: Engine,
    committed_session: Session,
) -> None:
    _create_example(committed_session, title="draft")
    active = _create_example(committed_session, title="active")
    active.status = ExampleStatus.ACTIVE
    committed_session.commit()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(ScopedCrudController(prefix="/scoped-examples", tags=["examples"]).router)

    def override_session() -> Iterator[Session]:
        with Session(bind=db_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app, raise_server_exceptions=False) as client:
        indexed = client.get("/scoped-examples")
        created = client.post(
            "/scoped-examples",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(title="normalized"),
        )

    assert [item["attributes"]["title"] for item in indexed.json()["data"]] == ["active"]
    assert created.status_code == 201
    assert created.json()["data"]["attributes"]["title"] == "NORMALIZED"


def test_example_query_policy_matches_the_public_allowlist() -> None:
    assert EXAMPLE_QUERY_POLICY.filters["title"].operators == frozenset({"exact", "contains"})
    assert EXAMPLE_QUERY_POLICY.filters["category.id"].operators == frozenset({"exact", "in", "isNull"})
    assert EXAMPLE_QUERY_POLICY.filters["createdAt"].operators == frozenset({"exact", "gt", "gte", "lt", "lte"})

    category_id = uuid4()
    spec = parse_query(
        QueryParams(f"filter[category.id]={category_id}&filter[createdAt][gte]=2026-07-14T00:00:00%2B09:00"),
        EXAMPLE_QUERY_POLICY,
    )
    assert spec.filters[0].value == category_id
    assert spec.filters[1].value == datetime.fromisoformat("2026-07-14T00:00:00+09:00")

    with pytest.raises(JsonApiException, match="INVALID_FILTER"):
        parse_query(QueryParams("filter[title][in]=one,two"), EXAMPLE_QUERY_POLICY)
    with pytest.raises(JsonApiException, match="INVALID_FILTER"):
        parse_query(QueryParams("filter[createdAt]=2026-07-14T00:00:00"), EXAMPLE_QUERY_POLICY)


def test_show_and_patch_use_concrete_jsonapi_documents(
    crud_client: TestClient,
    committed_session: Session,
) -> None:
    model = _create_example(committed_session, title="before")

    shown = crud_client.get(f"/api/v1/examples/{model.id}")
    updated = crud_client.patch(
        f"/api/v1/examples/{model.id}",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json={
            "data": {
                "type": "examples",
                "id": str(model.id),
                "attributes": {"title": "after"},
            }
        },
    )

    assert shown.status_code == 200
    assert shown.json()["data"]["id"] == str(model.id)
    assert updated.status_code == 200
    assert updated.json()["data"]["attributes"]["title"] == "after"
    assert ExampleCrudController.hook_log == ["before_update", "after_update"]


@pytest.mark.parametrize(
    ("data_overrides", "expected_code"),
    [
        ({"type": "wrong"}, "TYPE_MISMATCH"),
        ({"id": str(uuid4())}, "ID_MISMATCH"),
    ],
)
def test_patch_rejects_type_and_id_mismatch(
    crud_client: TestClient,
    committed_session: Session,
    data_overrides: dict[str, str],
    expected_code: str,
) -> None:
    model = _create_example(committed_session, title="before")
    data: dict[str, Any] = {
        "type": "examples",
        "id": str(model.id),
        "attributes": {"title": "after"},
    }
    data.update(data_overrides)

    response = crud_client.patch(
        f"/api/v1/examples/{model.id}",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json={"data": data},
    )

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == expected_code


def test_patch_allows_empty_attributes_as_a_noop(
    crud_client: TestClient,
    committed_session: Session,
) -> None:
    model = _create_example(committed_session, title="before")

    response = crud_client.patch(
        f"/api/v1/examples/{model.id}",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json={"data": {"type": "examples", "id": str(model.id), "attributes": {}}},
    )

    assert response.status_code == 200
    assert response.json()["data"]["attributes"]["title"] == "before"


def test_patch_rejects_resource_without_attributes_or_relationships(
    crud_client: TestClient,
    committed_session: Session,
) -> None:
    model = _create_example(committed_session, title="before")

    response = crud_client.patch(
        f"/api/v1/examples/{model.id}",
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
        json={"data": {"type": "examples", "id": str(model.id)}},
    )

    assert response.status_code == 422


def test_destroy_runs_hooks_and_returns_headerless_204(
    crud_client: TestClient,
    committed_session: Session,
) -> None:
    model = _create_example(committed_session, title="delete")
    resource_id = model.id

    response = crud_client.delete(f"/api/v1/examples/{resource_id}")

    assert response.status_code == 204
    assert response.content == b""
    assert "content-type" not in response.headers
    assert ExampleCrudController.hook_log == ["before_destroy", "after_destroy"]
    committed_session.expire_all()
    assert committed_session.get(Example, resource_id) is None


def test_missing_resource_uses_stable_jsonapi_error(crud_client: TestClient) -> None:
    response = crud_client.get(f"/api/v1/examples/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "RESOURCE_NOT_FOUND"


def test_openapi_exposes_concrete_write_document_schemas(crud_app: FastAPI) -> None:
    schema_document = crud_app.openapi()
    operation = schema_document["paths"]["/api/v1/examples"]["post"]
    schema = operation["requestBody"]["content"][JSONAPI_MEDIA_TYPE]["schema"]

    assert "$ref" in schema
    assert "BaseModel" not in schema["$ref"]
    assert "/api/v1/examples/{resource_id}" in schema_document["paths"]
    assert "403" in operation["responses"]

    operations = (
        ("/api/v1/examples", "get", "200"),
        ("/api/v1/examples", "post", "201"),
        ("/api/v1/examples/{resource_id}", "get", "200"),
        ("/api/v1/examples/{resource_id}", "patch", "200"),
    )
    for path, method, status in operations:
        success_content = schema_document["paths"][path][method]["responses"][status]["content"]
        assert set(success_content) == {JSONAPI_MEDIA_TYPE}
        assert success_content[JSONAPI_MEDIA_TYPE]["schema"] != {}

        error_content = schema_document["paths"][path][method]["responses"]["422"]["content"]
        assert set(error_content) == {JSONAPI_MEDIA_TYPE}
        assert error_content[JSONAPI_MEDIA_TYPE]["schema"]["$ref"].endswith("/ErrorDocument")

    destroy_responses = schema_document["paths"]["/api/v1/examples/{resource_id}"]["delete"]["responses"]
    assert "content" not in destroy_responses["204"]
    assert set(destroy_responses["400"]["content"]) == {JSONAPI_MEDIA_TYPE}


def test_resource_routes_apply_declared_read_and_write_dependencies(
    db_engine: Engine,
) -> None:
    dependency_log.clear()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(DependencyCrudController(prefix="/dependency-examples", tags=["examples"]).router)

    def override_session() -> Iterator[Session]:
        with Session(bind=db_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app, raise_server_exceptions=False) as client:
        read_response = client.get("/dependency-examples")
        write_response = client.post(
            "/dependency-examples",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(),
        )

    assert read_response.status_code == 200
    assert write_response.status_code == 201
    assert dependency_log == ["read", "write"]

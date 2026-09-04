"""Rails-style inherited CRUD action contract tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, ClassVar
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

import pytest
from fastapi import Body, Depends, FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, Engine, Integer, Select, event, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from starlette.datastructures import QueryParams
from starlette.responses import Response

from app.controllers.concerns.crud_actions import CrudActions
from app.controllers.concerns.jsonapi_routes import validate_route_prefix
from app.jsonapi import JSONAPI_MEDIA_TYPE, JsonApiException, ResourceIdentifier
from app.jsonapi.query import QueryPolicy, parse_query
from app.models import Example, ExampleCategory, ExampleStatus, ExampleTag
from app.schemas.example import (
    EXAMPLE_QUERY_POLICY,
    ExampleCreate,
    ExampleRelationships,
    ExampleReplace,
    ExampleUpdate,
)
from app.serializers import ExampleSerializer
from config.database import get_request_session


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


class ReadOnlyExampleController(CrudActions[Example, BaseModel, BaseModel, BaseModel]):
    """쓰기 스키마를 하나도 선언하지 않는 읽기 전용 컨트롤러.

    ``relationships_schema``를 일부러 선언한다 — ``enable_writes = False``가
    관계 쓰기 라우트까지 막지 못하면 이 선언이 그 구멍을 드러낸다.
    """

    model_class = Example
    serializer_class = ExampleSerializer
    relationships_schema = ExampleRelationships
    query_policy = EXAMPLE_QUERY_POLICY
    enable_writes = False


class CreateSerializationFailureController(ExampleCrudController):
    """Force a serializer failure inside the write transaction.

    ``tags`` is expired because it has no ``linkage_attribute``: its linkage cannot be
    derived from a local foreign key, so an unloaded ``tags`` is still the one relationship
    that makes serialization fail after the hooks ran.
    """

    def after_create(self, session: Session, model: Example, attributes: ExampleCreate) -> None:
        del attributes
        session.expire(model, ["tags"])


class UpdateSerializationFailureController(ExampleCrudController):
    """Force a serializer failure inside the update transaction; see the create twin."""

    def after_update(self, session: Session, model: Example, attributes: ExampleUpdate) -> None:
        del attributes
        session.expire(model, ["tags"])


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


_REQUEST_SESSION_DEPENDENCY = Depends(get_request_session)

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
def crud_app(minimal_app_factory: Callable[..., FastAPI]) -> FastAPI:
    ExampleCrudController.hook_log.clear()
    return minimal_app_factory(ExampleCrudController(prefix="/api/v1/examples", tags=["examples"]).router)


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
    committed_session: Session,
    minimal_app_factory: Callable[..., FastAPI],
) -> None:
    app = minimal_app_factory(RollbackController(prefix="/rollback-examples", tags=["examples"]).router)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/rollback-examples",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(),
        )

    assert response.status_code == 422
    assert committed_session.scalar(select(func.count()).select_from(Example)) == 0


def test_create_rolls_back_when_response_serialization_fails(
    committed_session: Session,
    minimal_app_factory: Callable[..., FastAPI],
) -> None:
    app = minimal_app_factory(
        CreateSerializationFailureController(prefix="/serialization-examples", tags=["examples"]).router
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/serialization-examples",
            headers={"Content-Type": JSONAPI_MEDIA_TYPE},
            json=_document(),
        )

    assert response.status_code == 500
    assert committed_session.scalar(select(func.count()).select_from(Example)) == 0


def test_update_rolls_back_when_response_serialization_fails(
    committed_session: Session,
    minimal_app_factory: Callable[..., FastAPI],
) -> None:
    model = _create_example(committed_session, title="변경 전")
    app = minimal_app_factory(
        UpdateSerializationFailureController(prefix="/serialization-examples", tags=["examples"]).router
    )
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


def test_body_bearing_delete_rejects_content_type_before_deserialization(
    minimal_app_factory: Callable[..., FastAPI],
) -> None:
    app = minimal_app_factory(BodyDeleteController(prefix="/api/v1/examples", tags=["examples"]).router)
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
        "/api/v1/examples?filter[title][contains]=alp&sort=-score&page[number]=1&page[size]=1&page[totals]=true",
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
    committed_session: Session,
    minimal_app_factory: Callable[..., FastAPI],
) -> None:
    _create_example(committed_session, title="draft")
    active = _create_example(committed_session, title="active")
    active.status = ExampleStatus.ACTIVE
    committed_session.commit()

    app = minimal_app_factory(ScopedCrudController(prefix="/scoped-examples", tags=["examples"]).router)
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


def test_put_is_not_registered_without_enable_upsert(
    crud_app: FastAPI,
    crud_client: TestClient,
    committed_session: Session,
) -> None:
    """``enable_upsert`` defaults to False, so no PUT route may exist at all."""

    model = _create_example(committed_session, title="업서트 없음")

    response = crud_client.put(
        f"/api/v1/examples/{model.id}",
        headers={"Accept": JSONAPI_MEDIA_TYPE, "Content-Type": JSONAPI_MEDIA_TYPE},
        json=_document(),
    )

    assert response.status_code == 405
    assert "put" not in crud_app.openapi()["paths"]["/api/v1/examples/{resource_id}"]


def test_write_operations_omit_auth_responses_without_write_dependencies(
    crud_app: FastAPI,
) -> None:
    """401/403 and ``security`` are injected only when write dependencies are declared."""

    paths = crud_app.openapi()["paths"]
    write_operations = (
        paths["/api/v1/examples"]["post"],
        paths["/api/v1/examples/{resource_id}"]["patch"],
        paths["/api/v1/examples/{resource_id}"]["delete"],
        paths["/api/v1/examples/{resource_id}/relationships/tags"]["post"],
        paths["/api/v1/examples/{resource_id}/relationships/tags"]["patch"],
        paths["/api/v1/examples/{resource_id}/relationships/tags"]["delete"],
    )

    for operation in write_operations:
        assert "401" not in operation["responses"]
        assert "security" not in operation
    assert "403" in paths["/api/v1/examples"]["post"]["responses"]
    assert "403" not in paths["/api/v1/examples/{resource_id}"]["patch"]["responses"]


def test_resource_routes_apply_declared_read_and_write_dependencies(
    minimal_app_factory: Callable[..., FastAPI],
) -> None:
    dependency_log.clear()
    app = minimal_app_factory(DependencyCrudController(prefix="/dependency-examples", tags=["examples"]).router)
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


@pytest.mark.parametrize("prefix", ["examples", "/examples/", "", "/"])
def test_validate_route_prefix_rejects_missing_leading_or_trailing_slash(prefix: str) -> None:
    with pytest.raises(ValueError, match="route prefix must start with '/' and must not end with '/'"):
        validate_route_prefix(prefix)


def test_validate_route_prefix_returns_a_well_formed_prefix() -> None:
    assert validate_route_prefix("/api/v1/examples") == "/api/v1/examples"


@pytest.mark.parametrize("prefix", ["examples/", "", "/"])
def test_crud_controller_construction_rejects_a_malformed_prefix(prefix: str) -> None:
    with pytest.raises(ValueError, match="route prefix must start with '/' and must not end with '/'"):
        ExampleCrudController(prefix=prefix, tags=["examples"])


def test_endpoint_without_manual_state_binding_still_rolls_back_an_integrity_error(
    committed_session: Session,
    minimal_app_factory: Callable[..., FastAPI],
) -> None:
    """The session dependency binds ``request.state.session`` so endpoints cannot forget it."""

    committed_session.add(ExampleCategory(name="중복 분류"))
    committed_session.commit()

    app = minimal_app_factory()

    @app.post("/unbound-categories", status_code=201)
    def create_category(session: Session = _REQUEST_SESSION_DEPENDENCY) -> Response:
        with session.begin():
            session.add(ExampleCategory(name="중복 분류"))
        return Response(status_code=201)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/unbound-categories")

    assert response.status_code == 409
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json()["errors"][0]["code"] == "RESOURCE_CONFLICT"
    assert "uq_example_categories_name" not in response.text
    assert "INSERT INTO" not in response.text
    assert committed_session.scalar(select(func.count()).select_from(ExampleCategory)) == 1


def test_request_session_dependency_publishes_the_endpoint_session_on_request_state(
    db_engine: Engine,
    minimal_app_factory: Callable[..., FastAPI],
) -> None:
    observed: dict[str, object] = {}

    opened: list[Session] = []

    def open_recorded_session() -> Session:
        session = Session(bind=db_engine, expire_on_commit=False)
        opened.append(session)
        return session

    app = minimal_app_factory(session_factory=open_recorded_session, register_handlers=False)

    @app.get("/bound-session")
    def read_session(request: Request, session: Session = _REQUEST_SESSION_DEPENDENCY) -> Response:
        observed["state"] = request.state.session
        observed["session"] = session
        return Response(status_code=204)

    with TestClient(app) as client:
        assert client.get("/bound-session").status_code == 204

    assert observed["state"] is opened[0]
    assert observed["session"] is opened[0]


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


def _selects(statements: Sequence[str]) -> list[str]:
    return [statement for statement in statements if statement.startswith("SELECT ")]


def _counts(statements: Sequence[str]) -> list[str]:
    return [statement for statement in statements if "count(" in statement.lower()]


def _link_query(link: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(link).query, keep_blank_values=True))


def test_index_without_totals_executes_no_count_query(
    crud_client: TestClient,
    committed_session: Session,
    db_engine: Engine,
) -> None:
    """A default list request must not pay for a COUNT.

    Two SELECTs remain: the page itself and the ``tags`` linkage load the serializer
    always needs. The count round trip that used to sit between them is gone.
    """

    _create_example(committed_session, title="하나")
    _create_example(committed_session, title="둘")

    with _recorded_statements(db_engine) as statements:
        response = crud_client.get("/api/v1/examples?page[size]=1")

    assert response.status_code == 200
    body = response.json()
    assert _counts(statements) == []
    assert len(_selects(statements)) == 2
    assert "meta" not in body
    assert body["links"]["last"] is None
    assert body["links"]["next"] is not None


def test_index_with_page_totals_reports_total_count_and_last_link(
    crud_client: TestClient,
    committed_session: Session,
    db_engine: Engine,
) -> None:
    for title in ("하나", "둘", "셋"):
        _create_example(committed_session, title=title)

    with _recorded_statements(db_engine) as statements:
        response = crud_client.get("/api/v1/examples?page[size]=2&page[totals]=true")

    assert response.status_code == 200
    body = response.json()
    assert len(_counts(statements)) == 1
    assert len(_selects(statements)) == 3
    assert body["meta"] == {"totalCount": 3}
    assert _link_query(body["links"]["last"])["page[number]"] == "2"
    for link in body["links"].values():
        assert link is None or _link_query(link)["page[totals]"] == "true"


def test_index_cursor_pagination_walks_pages_in_both_directions(
    crud_client: TestClient,
    committed_session: Session,
    db_engine: Engine,
) -> None:
    for index in range(5):
        _create_example(committed_session, title=f"커서 {index}")

    offset_ids = [
        resource["id"]
        for page_number in (1, 2, 3)
        for resource in crud_client.get(f"/api/v1/examples?page[size]=2&page[number]={page_number}").json()["data"]
    ]

    walked: list[list[str]] = []
    links: dict[str, str | None] = {"next": "/api/v1/examples?page[after]=&page[size]=2"}
    with _recorded_statements(db_engine) as statements:
        # Bounded so a cursor that fails to advance fails the test instead of hanging it.
        for _ in range(len(offset_ids) + 1):
            next_link = links["next"]
            if next_link is None:
                break
            body = crud_client.get(next_link).json()
            walked.append([resource["id"] for resource in body["data"]])
            links = body["links"]
        else:
            pytest.fail("the cursor walk did not terminate")

    assert [identifier for page in walked for identifier in page] == offset_ids
    assert len(offset_ids) == len(set(offset_ids)) == 5
    assert [len(page) for page in walked] == [2, 2, 1]
    assert not any("OFFSET" in statement for statement in statements)
    assert _counts(statements) == []

    # ``last`` addresses the final window of ``page[size]`` rows, so it overlaps the
    # forward walk's short final page rather than reproducing it.
    last_page = [resource["id"] for resource in crud_client.get(str(links["last"])).json()["data"]]
    assert last_page == offset_ids[-2:]

    backwards: list[list[str]] = []
    previous = links["prev"]
    for _ in range(len(offset_ids)):
        if previous is None:
            break
        body = crud_client.get(previous).json()
        backwards.insert(0, [resource["id"] for resource in body["data"]])
        previous = body["links"]["prev"]
    else:
        pytest.fail("the backwards cursor walk did not terminate")

    assert backwards == walked[:-1]


@pytest.mark.parametrize(
    ("query", "parameter"),
    [
        ("page[after]=!!!", "page[after]"),
        ("page[after]=e30", "page[after]"),
        (
            "sort=title&page[after]=eyJzIjpbIi1zY29yZSIsImlkIl0sInYiOlsiMSIsIjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMSJdfQ",
            "page[after]",
        ),
        ("page[after]=&page[before]=", "page[before]"),
        ("page[after]=&page[number]=2", "page[number]"),
        ("page[totals]=yes", "page[totals]"),
        ("page[after]=&page[after]=", "page[after]"),
    ],
)
def test_index_rejects_unauthorized_cursors_as_invalid_page(
    crud_client: TestClient,
    query: str,
    parameter: str,
) -> None:
    response = crud_client.get(f"/api/v1/examples?{query}")

    assert response.status_code == 400
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json()["errors"][0]["code"] == "INVALID_PAGE"
    assert response.json()["errors"][0]["source"]["parameter"] == parameter


class RowMultiplyingScopeController(ExampleCrudController):
    """A read scope that joins a to-many, so the database returns one row per tag.

    ``index_scope`` is the advertised read-scope extension point and nothing stops a
    scope from multiplying rows, so the collection walk has to stay complete when one
    does. The probe row is read before de-duplication for exactly this reason.
    """

    def index_scope(self, statement: Select[Any]) -> Select[Any]:
        return statement.join(Example.tags)


class _UnrepresentableSortBase(DeclarativeBase):
    """Declarative base giving the sort gate a real, cursor-unrepresentable column type.

    No application model has one, and the table is never created: a keyset cursor over
    this sort is refused while parsing the query string, before any SQL is built.
    """


class _UnrepresentableSortRow(_UnrepresentableSortBase):
    __tablename__ = "unrepresentable_sort_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)


class UnrepresentableSortController(ExampleCrudController):
    query_policy = QueryPolicy(
        filters=EXAMPLE_QUERY_POLICY.filters,
        sorts={**EXAMPLE_QUERY_POLICY.sorts, "payload": _UnrepresentableSortRow.payload},
        includes=EXAMPLE_QUERY_POLICY.includes,
        default_sort=EXAMPLE_QUERY_POLICY.default_sort,
        tie_breaker=EXAMPLE_QUERY_POLICY.tie_breaker,
    )


def _walk_collection(client: TestClient, first_link: str, *, budget: int) -> list[str]:
    seen: list[str] = []
    link: str | None = first_link
    for _ in range(budget):
        if link is None:
            return seen
        body = client.get(link).json()
        seen.extend(resource["id"] for resource in body["data"])
        link = body["links"]["next"]
    pytest.fail("the collection walk did not terminate")


def test_index_walk_reaches_every_resource_under_a_row_multiplying_scope(
    minimal_app_factory: Callable[..., FastAPI],
    committed_session: Session,
) -> None:
    """``has_more`` must come from the raw probe, not from the de-duplicated page.

    ``LIMIT size + 1`` is applied by PostgreSQL to joined rows, while ``.unique()`` folds
    them afterwards. Judging the probe after the fold ended the walk on the first page
    and stranded the rest of the collection.
    """

    expected: list[Example] = []
    for index in range(6):
        model = Example(
            title=f"다중 {index}",
            description=None,
            status=ExampleStatus.DRAFT,
            score=index,
            tags=[ExampleTag(name=f"tag-{index}-{position}") for position in range(3)],
        )
        committed_session.add(model)
        expected.append(model)
    committed_session.commit()

    app = minimal_app_factory(RowMultiplyingScopeController(prefix="/multiplied-examples", tags=["examples"]).router)
    with TestClient(app, raise_server_exceptions=False) as client:
        walked = _walk_collection(client, "/multiplied-examples?page[size]=2", budget=len(expected) * 3 + 2)

    assert set(walked) == {str(model.id) for model in expected}


@pytest.mark.parametrize("parameter", ["page[after]", "page[before]"])
def test_index_rejects_a_cursor_on_a_sort_the_codec_cannot_represent(
    minimal_app_factory: Callable[..., FastAPI],
    parameter: str,
) -> None:
    """The gate must refuse while parsing, before the page statement is ever built.

    Checking only nullability admitted the boundary cursor, so the request went on to
    query for a page whose ``next`` link ``encode_cursor`` could never mint.
    """

    app = minimal_app_factory(
        UnrepresentableSortController(prefix="/unrepresentable-examples", tags=["examples"]).router
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"/unrepresentable-examples?sort=payload&{parameter}=")

    assert response.status_code == 400
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    error = response.json()["errors"][0]
    assert error["code"] == "INVALID_PAGE"
    assert error["source"]["parameter"] == parameter


def test_read_only_controller_registers_only_read_routes() -> None:
    controller = ReadOnlyExampleController(prefix="/api/v1/examples", tags=["examples"])

    registered = {
        (route.path, method)
        for route in controller.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert registered == {
        ("/api/v1/examples", "GET"),
        ("/api/v1/examples/{resource_id}", "GET"),
        ("/api/v1/examples/{resource_id}/relationships/category", "GET"),
        ("/api/v1/examples/{resource_id}/category", "GET"),
        ("/api/v1/examples/{resource_id}/relationships/tags", "GET"),
        ("/api/v1/examples/{resource_id}/tags", "GET"),
    }


def test_read_only_controller_has_no_writable_relationship_names() -> None:
    controller = ReadOnlyExampleController(prefix="/api/v1/examples", tags=["examples"])

    # 선언된 relationships_schema가 있어도 비어 있어야 한다. 비어 있지 않으면
    # register_relationship_routes가 mutation 라우트를 등록한다.
    assert controller._writable_relationship_names == frozenset()


def test_write_controller_still_registers_every_route() -> None:
    controller = ExampleCrudController(prefix="/api/v1/examples", tags=["examples"])

    methods = {
        (route.path, method)
        for route in controller.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert ("/api/v1/examples", "POST") in methods
    assert ("/api/v1/examples/{resource_id}", "PATCH") in methods
    assert ("/api/v1/examples/{resource_id}", "DELETE") in methods
    assert ("/api/v1/examples/{resource_id}/relationships/tags", "POST") in methods

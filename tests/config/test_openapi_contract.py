"""Published schemas must validate real responses and expose query capabilities."""

from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from sqlalchemy.orm import Session

from app.models import Example, ExampleCategory, ExampleStatus, ExampleTag


def test_openapi_describes_query_allowlist_and_concrete_resources(app: FastAPI) -> None:
    document = app.openapi()
    operation = document["paths"]["/api/v1/examples"]["get"]
    parameters = {entry["name"]: entry for entry in operation["parameters"]}
    assert parameters["filter[score][gte]"]["schema"]["minimum"] == -(2**31)
    assert parameters["page[after]"]["schema"]["maxLength"] == 4096
    assert "category" in parameters["include"]["description"]
    resource = document["components"]["schemas"]["examplesResource"]
    assert resource["properties"]["attributes"]["properties"]["description"]["type"] == ["string", "null"]
    assert "relationships" in resource["required"]
    assert "relationships" not in document["components"]["schemas"]["exampleTagsResource"]["required"]


def test_published_schemas_validate_read_and_error_documents(client: TestClient, app: FastAPI) -> None:
    document = app.openapi()
    for path, query, status in [
        ("/api/v1/examples", "?include=", 200),
        ("/api/v1/examples", "?page[size]=bad", 400),
        ("/api/v1/categories", "", 200),
        ("/health/live", "", 200),
    ]:
        response = client.get(path + query)
        assert response.status_code == status
        schema: dict[str, Any] = document["paths"][path]["get"]["responses"][str(status)]["content"][
            "application/vnd.api+json"
        ]["schema"]
        Draft202012Validator({**schema, "components": document["components"]}).validate(response.json())


def test_published_write_schemas_accept_sparse_and_full_documents_and_reject_invalid_shapes(app: FastAPI) -> None:
    document = app.openapi()
    identifier = str(uuid4())
    for method, path, valid in [
        (
            "post",
            "/api/v1/examples",
            {"type": "examples", "attributes": {"title": "Title", "status": "draft", "score": 42}},
        ),
        (
            "put",
            "/api/v1/examples/{resource_id}",
            {
                "type": "examples",
                "id": identifier,
                "attributes": {"title": "Title", "description": None, "status": "draft", "score": 42},
            },
        ),
        ("patch", "/api/v1/examples/{resource_id}", {"type": "examples", "id": identifier, "attributes": {}}),
        (
            "patch",
            "/api/v1/examples/{resource_id}",
            {"type": "examples", "id": identifier, "relationships": {"category": {"data": None}}},
        ),
    ]:
        schema = document["paths"][path][method]["requestBody"]["content"]["application/vnd.api+json"]["schema"]
        validator = Draft202012Validator({**schema, "components": document["components"]})
        validator.validate({"data": valid})
        for invalid in [
            {"data": {**valid, "unknown": True}},
            {"data": valid, "meta": {}},
            {"data": {"type": "examples", "id": identifier}},
        ]:
            with pytest.raises(ValidationError):
                validator.validate(invalid)


def test_published_schemas_validate_resources_compound_and_nullable_relationships(
    client: TestClient, app: FastAPI, committed_session: Session
) -> None:
    category = ExampleCategory(name="Category")
    tag = ExampleTag(name="Tag")
    example = Example(
        title="Documented", description=None, status=ExampleStatus.DRAFT, score=42, category=category, tags=[tag]
    )
    empty = Example(title="No category", description=None, status=ExampleStatus.ACTIVE, score=1)
    committed_session.add_all([example, empty])
    committed_session.commit()
    document = app.openapi()
    for template, path in [
        ("/api/v1/examples", "/api/v1/examples?include=category,tags&page[totals]=true"),
        ("/api/v1/examples/{resource_id}", f"/api/v1/examples/{example.id}"),
        ("/api/v1/examples/{resource_id}/category", f"/api/v1/examples/{empty.id}/category"),
        ("/api/v1/examples/{resource_id}/category", f"/api/v1/examples/{example.id}/category"),
        (
            "/api/v1/examples/{resource_id}/relationships/category",
            f"/api/v1/examples/{example.id}/relationships/category",
        ),
        ("/api/v1/examples/{resource_id}/tags", f"/api/v1/examples/{example.id}/tags"),
    ]:
        response = client.get(path)
        assert response.status_code == 200
        schema = document["paths"][template]["get"]["responses"]["200"]["content"]["application/vnd.api+json"]["schema"]
        Draft202012Validator({**schema, "components": document["components"]}).validate(response.json())

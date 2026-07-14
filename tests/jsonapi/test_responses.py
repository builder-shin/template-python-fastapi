"""JSON:API response contract tests."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.jsonapi import (
    JSONAPI_MEDIA_TYPE,
    ErrorDocument,
    ErrorObject,
    JsonApiDocument,
    JsonApiResponse,
    RelationshipObject,
    ResourceObject,
    SuccessDocument,
)


def _schema_accepts_null(schema: dict[str, object]) -> bool:
    if schema.get("type") == "null":
        return True
    variants = schema.get("anyOf")
    return isinstance(variants, list) and any(
        isinstance(variant, dict) and _schema_accepts_null(variant) for variant in variants
    )


def test_jsonapi_response_uses_vendor_media_type_without_charset() -> None:
    response = JsonApiResponse(SuccessDocument(data=[]))

    assert response.media_type == JSONAPI_MEDIA_TYPE == "application/vnd.api+json"
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert json.loads(response.body) == {"data": [], "jsonapi": {"version": "1.1"}}


def test_jsonapi_response_serializes_aliases_and_excludes_optional_nulls() -> None:
    response = JsonApiResponse(
        ErrorDocument(
            errors=[ErrorObject(status="404", code="NOT_FOUND", title="없음")],
            meta={"request_id": "request-1"},
        ),
        status_code=404,
    )

    assert response.status_code == 404
    assert json.loads(response.body) == {
        "errors": [{"status": "404", "code": "NOT_FOUND", "title": "없음"}],
        "meta": {"request_id": "request-1"},
        "jsonapi": {"version": "1.1"},
    }


def test_error_document_and_response_preserve_minimal_valid_error_objects() -> None:
    document = ErrorDocument(
        errors=[
            ErrorObject(code="INVALID"),
            ErrorObject(status="422", title="속성 오류"),
            ErrorObject(source={"pointer": "/data/attributes/title"}),
            ErrorObject(meta={"request_id": "request-1"}),
        ],
    )
    expected_errors = [
        {"code": "INVALID"},
        {"status": "422", "title": "속성 오류"},
        {"source": {"pointer": "/data/attributes/title"}},
        {"meta": {"request_id": "request-1"}},
    ]

    assert document.model_dump(mode="json")["errors"] == expected_errors
    assert json.loads(JsonApiResponse(document).body)["errors"] == expected_errors


@pytest.mark.parametrize(
    "document",
    [JsonApiDocument(data=None), SuccessDocument(data=None)],
)
def test_jsonapi_response_preserves_explicit_null_data(document: JsonApiDocument) -> None:
    response = JsonApiResponse(document)

    assert json.loads(response.body)["data"] is None


def test_jsonapi_response_preserves_nested_explicit_null_relationship_data() -> None:
    response = JsonApiResponse(
        SuccessDocument(
            data=ResourceObject(
                type="examples",
                id="one",
                relationships={"category": {"data": None}},
            ),
        ),
    )

    assert json.loads(response.body)["data"]["relationships"]["category"] == {"data": None}


def test_fastapi_response_model_exclude_none_preserves_allowed_data_nulls() -> None:
    application = FastAPI()

    @application.get("/document", response_model=SuccessDocument, response_model_exclude_none=True)
    def get_document() -> SuccessDocument:
        return SuccessDocument(
            data=ResourceObject(
                type="examples",
                id="one",
                relationships={"category": RelationshipObject(data=None)},
            ),
        )

    response = TestClient(application).get("/document")

    assert response.json()["data"]["relationships"]["category"] == {"data": None}


def test_fastapi_openapi_does_not_advertise_non_data_null_members() -> None:
    application = FastAPI()

    @application.get("/document", response_model=SuccessDocument)
    def get_document() -> SuccessDocument:
        return SuccessDocument(data=[])

    schemas = application.openapi()["components"]["schemas"]
    schema_fields = {
        "LinkObject": ("rel", "describedby", "title", "type", "hreflang", "meta"),
        "ErrorSource": ("pointer", "parameter", "header"),
        "RelationshipObject": ("links", "meta"),
        "ResourceObject": ("meta", "relationships", "links"),
        "ErrorObject": ("id", "status", "code", "title", "detail", "source", "links", "meta"),
        "SuccessDocument": ("errors", "meta", "links", "included"),
    }

    for schema_name, field_names in schema_fields.items():
        for field_name in field_names:
            field_schema = schemas[schema_name]["properties"][field_name]
            assert "default" not in field_schema
            assert not _schema_accepts_null(field_schema)


def test_jsonapi_response_never_contains_missing_sentinel() -> None:
    response = JsonApiResponse(
        SuccessDocument(data=ResourceObject(type="examples", id="one")),
    )

    assert b"MISSING" not in response.body


@pytest.mark.parametrize("header_name", ["Content-Type", "content-type"])
def test_jsonapi_response_overwrites_caller_content_type(header_name: str) -> None:
    response = JsonApiResponse(
        SuccessDocument(data=[]),
        headers={header_name: "application/json; charset=utf-8"},
    )

    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE


def test_jsonapi_response_preserves_attribute_key_spelling() -> None:
    response = JsonApiResponse(
        SuccessDocument(
            data=ResourceObject(
                type="examples",
                id="one",
                attributes={"created_at": "2026-07-14T00:00:00Z"},
            ),
        ),
    )

    assert json.loads(response.body)["data"]["attributes"] == {
        "created_at": "2026-07-14T00:00:00Z",
    }

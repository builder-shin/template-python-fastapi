"""JSON:API response contract tests."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.jsonapi import (
    JSONAPI_MEDIA_TYPE,
    ErrorDocument,
    ErrorObject,
    JsonApiDocument,
    JsonApiResponse,
    LinkObject,
    RelationshipDocument,
    RelationshipObject,
    ResourceIdentifier,
    ResourceObject,
    SuccessDocument,
)


def _json_body(response: JsonApiResponse) -> Any:
    """Decode a rendered body; Starlette types `Response.body` as bytes | memoryview."""

    return json.loads(bytes(response.body))


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
    assert _json_body(response) == {"data": [], "jsonapi": {"version": "1.1"}}


def test_jsonapi_response_serializes_aliases_and_excludes_optional_nulls() -> None:
    response = JsonApiResponse(
        ErrorDocument(
            errors=[ErrorObject(status="404", code="NOT_FOUND", title="없음")],
            meta={"request_id": "request-1"},
        ),
        status_code=404,
    )

    assert response.status_code == 404
    assert _json_body(response) == {
        "errors": [{"status": "404", "code": "NOT_FOUND", "title": "없음"}],
        "meta": {"request_id": "request-1"},
        "jsonapi": {"version": "1.1"},
    }


def test_error_document_and_response_preserve_minimal_valid_error_objects() -> None:
    document = ErrorDocument(
        errors=[
            ErrorObject(code="INVALID"),
            ErrorObject(status="422", title="속성 오류"),
            # Pydantic coerces the mapping into ErrorSource; mypy only sees the declared model.
            ErrorObject(source={"pointer": "/data/attributes/title"}),  # type: ignore[arg-type]
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
    assert _json_body(JsonApiResponse(document))["errors"] == expected_errors


@pytest.mark.parametrize(
    "document",
    [JsonApiDocument(data=None), SuccessDocument(data=None)],
)
def test_jsonapi_response_preserves_explicit_null_data(document: JsonApiDocument) -> None:
    response = JsonApiResponse(document)

    assert _json_body(response)["data"] is None


def test_jsonapi_response_preserves_nested_explicit_null_relationship_data() -> None:
    response = JsonApiResponse(
        SuccessDocument(
            data=ResourceObject(
                type="examples",
                id="one",
                # Pydantic coerces the mapping into RelationshipObject.
                relationships={"category": {"data": None}},  # type: ignore[dict-item]
            ),
        ),
    )

    assert _json_body(response)["data"]["relationships"]["category"] == {"data": None}


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

    assert _json_body(response)["data"]["attributes"] == {
        "created_at": "2026-07-14T00:00:00Z",
    }


def test_jsonapi_response_body_pins_the_compound_document_wire_bytes() -> None:
    document = SuccessDocument(
        data=ResourceObject(
            type="examples",
            id="one",
            attributes={"title": "예시", "rate": 1e-5, "created_at": "2026-07-14T00:00:00Z"},
            relationships={
                "category": RelationshipObject(
                    data=ResourceIdentifier(type="categories", id="c-1"),
                    links={"self": LinkObject(href="/examples/one/relationships/category")},
                ),
                "tags": RelationshipObject(
                    data=[
                        ResourceIdentifier(type="tags", id="t-1"),
                        ResourceIdentifier(type="tags", id="t-2"),
                    ],
                ),
            },
            links={"self": "/examples/one"},
        ),
        included=[
            ResourceObject(type="categories", id="c-1", attributes={"name": "카테고리"}),
            ResourceObject(type="tags", id="t-1"),
            ResourceObject(type="tags", id="t-2"),
        ],
        links={"self": "/examples/one"},
    )

    response = JsonApiResponse(document)

    # Pinned as literal wire bytes on purpose: comparing against
    # `document.model_dump_json(...)` would re-derive the implementation and could never
    # detect a rendering drift. Member order, `exclude_none`, unescaped non-ASCII, the
    # compact separators and pydantic-core float formatting are all part of this contract.
    assert (
        response.body
        == (
            '{"data":{"type":"examples","id":"one","attributes":'
            '{"title":"예시","rate":0.00001,"created_at":"2026-07-14T00:00:00Z"},'
            '"relationships":{"category":{"data":{"type":"categories","id":"c-1"},'
            '"links":{"self":{"href":"/examples/one/relationships/category"}}},'
            '"tags":{"data":[{"type":"tags","id":"t-1"},{"type":"tags","id":"t-2"}]}},'
            '"links":{"self":"/examples/one"}},'
            '"jsonapi":{"version":"1.1"},'
            '"links":{"self":"/examples/one"},'
            '"included":[{"type":"categories","id":"c-1","attributes":{"name":"카테고리"}},'
            '{"type":"tags","id":"t-1","attributes":{}},'
            '{"type":"tags","id":"t-2","attributes":{}}]}'
        ).encode()
    )  # UTF-8; `render` encodes the same way.
    assert response.headers["content-length"] == str(len(response.body))
    assert _json_body(response)["included"][0]["attributes"] == {"name": "카테고리"}


def test_jsonapi_response_renders_small_floats_with_pydantic_core_formatting() -> None:
    # `model_dump_json` is pydantic-core, not `json.dumps`: for |v| < 1e-4 the byte width
    # differs from the repr `json.dumps` would emit (`0.00001` not `1e-05`, `1e-7` not
    # `1e-07`). The decoded double is identical, so this pins bytes, not values.
    response = JsonApiResponse(
        SuccessDocument(data=ResourceObject(type="examples", id="one", attributes={"rate": 1e-5, "tiny": 1e-7})),
    )

    assert response.body == (
        b'{"data":{"type":"examples","id":"one","attributes":{"rate":0.00001,"tiny":1e-7}},"jsonapi":{"version":"1.1"}}'
    )
    assert response.headers["content-length"] == str(len(response.body))
    assert json.loads(response.body)["data"]["attributes"] == {"rate": 1e-5, "tiny": 1e-7}


def test_jsonapi_response_preserves_relationship_document_explicit_null_data() -> None:
    response = JsonApiResponse(RelationshipDocument(data=None))

    assert _json_body(response)["data"] is None
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE


def test_jsonapi_response_emits_korean_detail_without_ascii_escaping() -> None:
    response = JsonApiResponse(
        ErrorDocument(
            errors=[
                ErrorObject(
                    status="404",
                    code="NOT_FOUND",
                    title="없음",
                    detail="리소스를 찾을 수 없습니다",
                ),
            ],
        ),
        status_code=404,
    )

    assert "리소스를 찾을 수 없습니다".encode() in response.body
    assert _json_body(response)["errors"][0]["detail"] == "리소스를 찾을 수 없습니다"
    assert response.headers["content-length"] == str(len(response.body))

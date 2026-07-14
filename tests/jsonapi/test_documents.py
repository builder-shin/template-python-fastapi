"""JSON:API 1.1 document model contract tests."""

from __future__ import annotations

import json
import math
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError
from typing_extensions import Sentinel

from app.jsonapi import (
    ErrorDocument,
    ErrorObject,
    JsonApiDocument,
    LinkObject,
    RelationshipObject,
    ResourceIdentifier,
    ResourceObject,
    SuccessDocument,
)
from app.jsonapi.documents import ErrorSource

NON_DATA_OPTIONAL_SCHEMA_FIELDS = [
    (LinkObject, ("rel", "describedby", "title", "type", "hreflang", "meta")),
    (RelationshipObject, ("links", "meta")),
    (ResourceObject, ("meta", "relationships", "links")),
    (ErrorSource, ("pointer", "parameter", "header")),
    (ErrorObject, ("id", "status", "code", "title", "detail", "source", "links", "meta")),
    (JsonApiDocument, ("errors", "meta", "links", "included")),
]


def _schema_accepts_null(schema: dict[str, object]) -> bool:
    if schema.get("type") == "null":
        return True
    for keyword in ("anyOf", "oneOf"):
        variants = schema.get(keyword)
        if isinstance(variants, list) and any(
            isinstance(variant, dict) and _schema_accepts_null(variant) for variant in variants
        ):
            return True
    return False


def test_success_document_serializes_jsonapi_version() -> None:
    document = SuccessDocument(
        data=ResourceObject(type="examples", id="abc", attributes={"title": "예시"}),
    )

    assert document.model_dump(mode="json", exclude_none=True)["jsonapi"] == {"version": "1.1"}


@pytest.mark.parametrize(
    "data",
    [
        ResourceObject(type="examples", id="one"),
        [ResourceObject(type="examples", id="one")],
        None,
    ],
)
def test_success_document_accepts_single_collection_or_null_data(
    data: ResourceObject | list[ResourceObject] | None,
) -> None:
    document = SuccessDocument(data=data)

    assert "data" in document.model_dump(mode="json", exclude_none=True)


def test_explicit_null_data_is_preserved_without_unset_optional_nulls() -> None:
    dumped = JsonApiDocument(data=None).model_dump(mode="json", exclude_none=True)

    assert dumped == {"data": None, "jsonapi": {"version": "1.1"}}


def test_nested_explicit_null_relationship_data_is_preserved() -> None:
    document = SuccessDocument(
        data=ResourceObject(
            type="examples",
            id="one",
            relationships={"category": RelationshipObject(data=None)},
        ),
    )

    dumped = document.model_dump(mode="json", exclude_none=True)

    assert dumped["data"]["relationships"]["category"] == {"data": None}


def test_model_dump_json_preserves_only_allowed_explicit_null_data() -> None:
    document = SuccessDocument(
        data=ResourceObject(
            type="examples",
            id="one",
            relationships={"category": RelationshipObject(data=None)},
        ),
    )

    dumped = json.loads(document.model_dump_json(exclude_none=True))

    assert dumped["data"]["relationships"]["category"] == {"data": None}


@pytest.mark.parametrize(
    ("model_type", "arguments"),
    [
        (ResourceObject, {"type": "examples", "id": "one", "relationships": None}),
        (ErrorObject, {"status": "400", "detail": None}),
        (RelationshipObject, {"data": None, "links": None}),
        (JsonApiDocument, {"data": [], "links": None}),
    ],
)
def test_models_reject_explicit_null_for_non_data_fields(
    model_type: type[BaseModel],
    arguments: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model_type(**arguments)


@pytest.mark.parametrize(
    ("model_type", "arguments"),
    [
        (LinkObject, {"href": "/examples", "rel": Sentinel("OTHER_LINK_REL")}),
        (
            ResourceIdentifier,
            {"type": "examples", "id": "one", "meta": Sentinel("OTHER_IDENTIFIER_META")},
        ),
        (RelationshipObject, {"data": None, "links": Sentinel("OTHER_RELATIONSHIP_LINKS")}),
        (
            ResourceObject,
            {"type": "examples", "id": "one", "relationships": Sentinel("OTHER_RELATIONSHIPS")},
        ),
        (ErrorSource, {"pointer": Sentinel("OTHER_SOURCE_POINTER")}),
        (ErrorObject, {"code": Sentinel("OTHER_ERROR_CODE")}),
        (JsonApiDocument, {"data": [], "links": Sentinel("OTHER_DOCUMENT_LINKS")}),
    ],
)
def test_models_reject_arbitrary_sentinel_for_non_data_fields(
    model_type: type[BaseModel],
    arguments: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model_type(**arguments)


@pytest.mark.parametrize(("model_type", "field_names"), NON_DATA_OPTIONAL_SCHEMA_FIELDS)
def test_non_data_optional_member_annotations_do_not_accept_sentinel(
    model_type: type[BaseModel],
    field_names: tuple[str, ...],
) -> None:
    for field_name in field_names:
        annotation = model_type.model_fields[field_name].annotation
        assert annotation is not Sentinel
        assert Sentinel not in get_args(annotation)


@pytest.mark.parametrize("mode", ["validation", "serialization"])
@pytest.mark.parametrize(("model_type", "field_names"), NON_DATA_OPTIONAL_SCHEMA_FIELDS)
def test_non_data_optional_members_are_omittable_but_not_nullable_in_schema(
    mode: str,
    model_type: type[BaseModel],
    field_names: tuple[str, ...],
) -> None:
    schema = model_type.model_json_schema(mode=mode)

    for field_name in field_names:
        field_schema = schema["properties"][field_name]
        assert "default" not in field_schema
        assert not _schema_accepts_null(field_schema)


def test_omitted_members_never_leak_missing_sentinel_into_public_dumps() -> None:
    resource = ResourceObject(
        type="examples",
        id="one",
        attributes={"nullable": None},
        links={"next": None},
    )
    relationship = RelationshipObject(links={})
    error = ErrorObject(status="400")
    document = JsonApiDocument(meta={"nullable": None})

    assert resource.model_dump(mode="json") == {
        "type": "examples",
        "id": "one",
        "attributes": {"nullable": None},
        "links": {"next": None},
    }
    assert relationship.model_dump(mode="json") == {"links": {}}
    assert error.model_dump(mode="json") == {"status": "400"}
    assert document.model_dump(mode="json") == {
        "meta": {"nullable": None},
        "jsonapi": {"version": "1.1"},
    }


def test_success_document_requires_data_member() -> None:
    with pytest.raises(ValidationError):
        SuccessDocument()


def test_document_rejects_explicit_null_data_and_errors_together() -> None:
    with pytest.raises(ValidationError, match="data and errors"):
        JsonApiDocument(
            data=None,
            errors=[ErrorObject(status="400", code="INVALID", title="오류")],
        )


def test_document_requires_data_errors_or_meta() -> None:
    with pytest.raises(ValidationError, match="data, errors, or meta"):
        JsonApiDocument(links={"self": "/examples"})


@pytest.mark.parametrize("member", ["meta", "errors"])
def test_document_does_not_count_null_as_a_required_member(member: str) -> None:
    with pytest.raises(ValidationError):
        JsonApiDocument(**{member: None})


def test_document_rejects_included_without_data_member() -> None:
    with pytest.raises(ValidationError, match="included requires data"):
        JsonApiDocument(
            meta={"count": 1},
            included=[ResourceObject(type="tags", id="tag-1")],
        )


def test_document_rejects_non_object_input() -> None:
    with pytest.raises(ValidationError):
        JsonApiDocument.model_validate([])


def test_error_document_requires_non_empty_errors() -> None:
    with pytest.raises(ValidationError):
        ErrorDocument(errors=[])


def test_error_object_requires_at_least_one_member() -> None:
    with pytest.raises(ValidationError, match="error object requires at least one member"):
        ErrorObject()


def test_error_document_rejects_empty_nested_error_object() -> None:
    with pytest.raises(ValidationError, match="error object requires at least one member"):
        ErrorDocument(errors=[{}])


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ErrorObject(code="INVALID"), {"code": "INVALID"}),
        (ErrorObject(status="422", title="속성 오류"), {"status": "422", "title": "속성 오류"}),
        (
            ErrorObject(source={"pointer": "/data/attributes/title"}),
            {"source": {"pointer": "/data/attributes/title"}},
        ),
        (ErrorObject(meta={"request_id": "request-1"}), {"meta": {"request_id": "request-1"}}),
    ],
)
def test_error_object_accepts_any_single_supported_member(
    error: ErrorObject,
    expected: dict[str, object],
) -> None:
    assert error.model_dump(mode="json") == expected


def test_models_reject_extra_members() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ResourceObject(type="examples", id="abc", unexpected=True)


def test_relationship_requires_data_links_or_meta() -> None:
    with pytest.raises(ValidationError, match="relationship requires"):
        RelationshipObject()


def test_relationship_does_not_accept_null_links_as_its_only_member() -> None:
    with pytest.raises(ValidationError):
        RelationshipObject(links=None)


def test_jsonapi_version_is_always_1_1() -> None:
    with pytest.raises(ValidationError):
        SuccessDocument(data=[], jsonapi={"version": "1.0"})


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_success_document_json_schema_keeps_concrete_data_and_jsonapi_structure(mode: str) -> None:
    schema = SuccessDocument.model_json_schema(mode=mode)

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["data"]
    assert schema["properties"]["data"] == {"$ref": "#/$defs/ResourceData"}
    assert schema["properties"]["jsonapi"] == {"$ref": "#/$defs/JsonApiVersion"}
    assert "ResourceObject" in schema["$defs"]


def test_null_data_serializers_respect_field_include_and_exclude() -> None:
    document = SuccessDocument(data=None)
    relationship = RelationshipObject(data=None)

    assert document.model_dump(mode="json", exclude_none=True, exclude={"data"}) == {
        "jsonapi": {"version": "1.1"},
    }
    assert document.model_dump(mode="json", exclude_none=True, include={"jsonapi"}) == {
        "jsonapi": {"version": "1.1"},
    }
    assert document.model_dump(mode="json", exclude_none=True, exclude={"data": {0}}) == {
        "data": None,
        "jsonapi": {"version": "1.1"},
    }
    assert document.model_dump(mode="json", exclude_none=True, exclude={"data": True}) == {
        "jsonapi": {"version": "1.1"},
    }
    assert relationship.model_dump(mode="json", exclude_none=True, exclude={"data"}) == {}


def test_data_collection_index_selection_uses_pydantic_handler_output() -> None:
    document = SuccessDocument(
        data=[
            ResourceObject(type="examples", id="one"),
            ResourceObject(type="examples", id="two"),
        ],
    )

    assert document.model_dump(
        mode="json",
        exclude_none=True,
        include={"data": {0: {"id"}}},
    ) == {"data": [{"id": "one"}]}
    assert document.model_dump(
        mode="json",
        exclude_none=True,
        exclude={"data": {0}},
    ) == {
        "data": [{"type": "examples", "id": "two", "attributes": {}}],
        "jsonapi": {"version": "1.1"},
    }


@pytest.mark.parametrize(
    "relationship_data",
    [
        ResourceIdentifier(type="categories", id="one"),
        [ResourceIdentifier(type="tags", id="one")],
        None,
    ],
)
def test_relationship_supports_single_collection_or_null_data(
    relationship_data: ResourceIdentifier | list[ResourceIdentifier] | None,
) -> None:
    relationship = RelationshipObject(
        data=relationship_data,
        links={"related": "/examples/one/relationships/tags"},
        meta={"editable": True},
    )

    assert relationship.data == relationship_data


def test_error_object_serializes_source_links_and_recursive_meta() -> None:
    error = ErrorObject(
        id="error-1",
        status="422",
        code="INVALID_ATTRIBUTE",
        title="속성 오류",
        detail="title이 필요합니다.",
        source={"pointer": "/data/attributes/title", "parameter": "title"},
        links={"about": "/errors/INVALID_ATTRIBUTE"},
        meta={"context": {"fields": ["title", None], "retryable": False}},
    )

    assert error.model_dump(mode="json", exclude_none=True) == {
        "id": "error-1",
        "status": "422",
        "code": "INVALID_ATTRIBUTE",
        "title": "속성 오류",
        "detail": "title이 필요합니다.",
        "source": {"pointer": "/data/attributes/title", "parameter": "title"},
        "links": {"about": "/errors/INVALID_ATTRIBUTE"},
        "meta": {"context": {"fields": ["title", None], "retryable": False}},
    }


def test_link_object_and_null_link_follow_jsonapi_1_1_shape() -> None:
    resource = ResourceObject(
        type="examples",
        id="abc",
        links={
            "self": LinkObject(
                href="/examples/abc",
                rel="self",
                describedby="/schemas/example",
                title="예시",
                type="application/vnd.api+json",
                hreflang=["ko", "en"],
                meta={"stable": True},
            ),
            "related": "/examples/abc/related",
            "next": None,
        },
    )

    assert resource.model_dump(mode="json", exclude_none=True)["links"] == {
        "self": {
            "href": "/examples/abc",
            "rel": "self",
            "describedby": "/schemas/example",
            "title": "예시",
            "type": "application/vnd.api+json",
            "hreflang": ["ko", "en"],
            "meta": {"stable": True},
        },
        "related": "/examples/abc/related",
        "next": None,
    }


def test_link_object_rejects_extra_members() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        LinkObject(href="/examples/abc", unexpected=True)


def test_error_source_supports_header() -> None:
    error = ErrorObject(
        status="400",
        source={"header": "X-Request-ID"},
    )

    assert error.model_dump(mode="json", exclude_none=True)["source"] == {
        "header": "X-Request-ID",
    }


def test_recursive_json_values_generate_schema_and_dump_stably() -> None:
    resource = ResourceObject(
        type="examples",
        id="abc",
        attributes={
            "configuration": {
                "steps": [1, "two", True, None, {"weight": 0.5}],
            },
        },
    )

    schema = ResourceObject.model_json_schema()
    dumped = resource.model_dump(mode="json", exclude_none=True)

    assert "$defs" in schema
    assert dumped["attributes"]["configuration"] == {
        "steps": [1, "two", True, None, {"weight": 0.5}],
    }


@pytest.mark.parametrize("non_finite", [math.nan, math.inf, -math.inf])
def test_json_values_reject_non_finite_numbers(non_finite: float) -> None:
    with pytest.raises(ValidationError):
        ResourceObject(type="examples", id="abc", attributes={"score": non_finite})

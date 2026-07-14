"""JSON:API request negotiation contract tests."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.jsonapi import JsonApiException, require_jsonapi_accept
from app.jsonapi.exception_handlers import register_exception_handlers
from app.jsonapi.negotiation import validate_accept, validate_content_type


@pytest.mark.parametrize(
    "accept",
    [
        None,
        "",
        " \t ",
        "*/*",
        "application/*",
        "application/vnd.api+json",
        " Application/Vnd.Api+Json ",
        "text/plain;q=0.1, application/vnd.api+json;q=0.8",
        'application/json; note="comma,inside", application/vnd.api+json;q=0.7',
        "application/vnd.api+json;q=0.7, application/*;q=0",
        'application/vnd.api+json; profile="https://example.com/profile"',
        'application/vnd.api+json;profile="https://example.com/one https://example.com/two";q=0.8',
    ],
)
def test_accept_allows_jsonapi_compatible_ranges(accept: str | None) -> None:
    validate_accept(accept)


@pytest.mark.parametrize(
    "accept",
    [
        "application/json",
        "application/vnd.api+json;q=0",
        "application/vnd.api+json; charset=utf-8",
        'application/vnd.api+json; ext="https://example.com/ext,one"',
        "application/vnd.api+json;q=invalid",
        'application/vnd.api+json;q="0.7"',
        "application/vnd.api+json;q = 0.7",
        "application/vnd.api+json;q= 0.7",
        "application/vnd.api+json;q=0.7 ",
        "application/vnd.api+json;q=.7",
        "application/vnd.api+json;q=0.1234",
        "application/vnd.api+json;q=1.1",
        "application/vnd.api+json;q=0.5;q=0.8",
        "application/vnd.api+json;ext=unsupported, */*;q=0.5",
        "application/vnd.api+json;q=0, application/*;q=1, */*;q=1",
        "application/*;q=0, */*;q=1",
        "application/*;q=0, */*;q=0",
        'application/*;profile="https://example.com/profile"',
        '*/*;profile="https://example.com/profile"',
        "application/vnd.api+json;q=0, application/json;q=1",
        ", ,",
    ],
)
def test_accept_rejects_ranges_that_cannot_receive_jsonapi(accept: str) -> None:
    with pytest.raises(JsonApiException) as captured:
        validate_accept(accept)

    assert captured.value.status_code == 406
    assert captured.value.code == "NOT_ACCEPTABLE"
    assert captured.value.source_header == "Accept"


@pytest.mark.parametrize(
    "content_type",
    [
        "application/vnd.api+json",
        " Application/Vnd.Api+Json ",
        "\tapplication/vnd.api+json\t",
        'application/vnd.api+json;profile="https://example.com/profile"',
        ' Application/Vnd.Api+Json ;profile="https://example.com/one https://example.com/two" ',
    ],
)
def test_content_type_allows_exact_vendor_media_type(content_type: str) -> None:
    validate_content_type(content_type)


@pytest.mark.parametrize(
    "content_type",
    [
        None,
        "",
        "application/json",
        "application/vnd.api+json; charset=utf-8",
        "application/vnd.api+json;ext=unsupported",
        "application/vnd.api+json;",
        "application/vnd.api+json, application/json",
    ],
)
def test_content_type_rejects_missing_wrong_or_parameterized_values(
    content_type: str | None,
) -> None:
    with pytest.raises(JsonApiException) as captured:
        validate_content_type(content_type)

    assert captured.value.status_code == 415
    assert captured.value.code == "UNSUPPORTED_MEDIA_TYPE"
    assert captured.value.source_header == "Content-Type"


def test_require_jsonapi_accept_is_usable_as_fastapi_header_dependency() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/dependency", dependencies=[Depends(require_jsonapi_accept)])
    def dependency_route() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)

    assert client.get("/dependency", headers={"Accept": "application/*"}).status_code == 200
    rejected = client.get(
        "/dependency",
        headers={"Accept": "application/json", "Accept-Language": "en-US"},
    )
    assert rejected.status_code == 406
    assert rejected.headers["content-type"] == "application/vnd.api+json"
    assert rejected.json()["errors"][0]["code"] == "NOT_ACCEPTABLE"

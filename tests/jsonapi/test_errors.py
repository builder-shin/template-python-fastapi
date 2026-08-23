"""JSON:API localization and exception handler contract tests."""

from __future__ import annotations

from typing import get_args
from unittest.mock import patch

import pytest
from fastapi import Cookie, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from app.jsonapi import JSONAPI_MEDIA_TYPE, JsonApiException
from app.jsonapi.errors import ERROR_CATALOG, ERROR_CODES, ErrorCode, Language
from app.jsonapi.exception_handlers import (
    handle_http_exception,
    handle_integrity_error,
    handle_jsonapi_exception,
    handle_request_validation,
    handle_unexpected_error,
    register_exception_handlers,
)
from app.jsonapi.localization import localize_error, resolve_language
from config.main import create_app

ERROR_CODE_LITERALS: tuple[ErrorCode, ...] = get_args(ErrorCode.__value__)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, "ko"),
        ("", "ko"),
        ("ko", "ko"),
        ("en", "en"),
        ("ko-KR", "ko"),
        ("en-US", "en"),
        ("ko;q=0.2, en;q=0.9", "en"),
        ("en;q=0.8, ko;q=0.8", "en"),
        ("ko;q=0.8, en;q=0.8", "ko"),
        ("en;q=0, ko;q=0.1", "ko"),
        ("en;q=0", "ko"),
        ("ko;q=0, *;q=1", "en"),
        ("ko;q=.5, *;q=1", "en"),
        ("ko;q=0.5, *;q=1", "en"),
        ("ko;q=0.9, ko-KR;q=0, en;q=0.5", "en"),
        ("*;q=1", "ko"),
        ("fr-FR, de;q=0.8", "ko"),
        ("en;q=broken", "ko"),
        ('en;q="0.9"', "ko"),
        ("en;q = 0.9", "ko"),
        ("en;q=1.1", "ko"),
    ],
)
def test_resolve_language_honors_supported_quality_preferences(
    header: str | None,
    expected: str,
) -> None:
    assert resolve_language(header) == expected


def test_error_catalog_has_full_design_code_parity_for_both_languages() -> None:
    literal_codes = set(ERROR_CODE_LITERALS)

    assert literal_codes
    assert set(ERROR_CODES) == literal_codes
    assert len(ERROR_CODES) == len(literal_codes)
    assert set(ERROR_CATALOG) == {"ko", "en"}
    assert set(ERROR_CATALOG["ko"]) == literal_codes
    assert set(ERROR_CATALOG["en"]) == literal_codes

    for language in ("ko", "en"):
        for message in ERROR_CATALOG[language].values():
            assert message.title
            assert message.detail


@pytest.mark.parametrize("code", ERROR_CODE_LITERALS)
@pytest.mark.parametrize("language", ["ko", "en"])
def test_every_error_code_localizes_in_both_languages(code: ErrorCode, language: Language) -> None:
    # No catalog detail uses ``{}`` format placeholders, so ``detail_args`` is not needed.
    # A future message with a placeholder must pass sample ``detail_args`` here.
    error = localize_error(code, language=language)

    assert error.code == code
    assert error.title
    assert error.detail


def test_localized_error_defaults_to_korean_and_supports_safe_override() -> None:
    korean = localize_error("RESOURCE_NOT_FOUND", language=resolve_language(None))
    english = localize_error(
        "RESOURCE_NOT_FOUND",
        language="en",
        status=404,
        source_pointer="/data/id",
        detail_override="The requested example does not exist.",
    )

    assert korean.title == "리소스를 찾을 수 없음"
    assert korean.code == "RESOURCE_NOT_FOUND"
    assert english.model_dump(mode="json") == {
        "status": "404",
        "code": "RESOURCE_NOT_FOUND",
        "title": "Resource not found",
        "detail": "The requested example does not exist.",
        "source": {"pointer": "/data/id"},
    }


def test_jsonapi_exception_stores_only_declared_safe_properties() -> None:
    exception = JsonApiException(
        status_code=400,
        code="INVALID_QUERY_PARAMETER",
        source_pointer="/data/attributes/title",
        source_parameter="filter[title]",
        source_header="X-Safe-Header",
        detail_args={"field": "title"},
        detail_override="The title is invalid.",
    )

    assert exception.status_code == 400
    assert exception.code == "INVALID_QUERY_PARAMETER"
    assert exception.source_pointer == "/data/attributes/title"
    assert exception.source_parameter == "filter[title]"
    assert exception.source_header == "X-Safe-Header"
    assert exception.detail_args == {"field": "title"}
    assert exception.detail_override == "The title is invalid."
    assert not hasattr(exception, "original_exception")


def test_jsonapi_exception_rejects_non_error_status_including_204() -> None:
    with pytest.raises(ValueError):
        JsonApiException(status_code=204, code="INTERNAL_SERVER_ERROR")


class ValidationItem(BaseModel):
    id: int


class ValidationData(BaseModel):
    escaped_member: int = Field(alias="a/b~c")
    items: list[ValidationItem]


class ValidationDocument(BaseModel):
    data: ValidationData


class RollbackSession:
    def __init__(self) -> None:
        self.rollback_calls = 0

    def rollback(self) -> None:
        self.rollback_calls += 1


def _create_handler_test_app(session: RollbackSession) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/validation")
    def validation_route(document: ValidationDocument) -> dict[str, bool]:
        return {"ok": bool(document.data.items)}

    @app.get("/typed")
    def typed_route() -> None:
        raise JsonApiException(
            status_code=400,
            code="INVALID_QUERY_PARAMETER",
            source_parameter="filter[score]",
            detail_override="The filter value is invalid.",
        )

    @app.get("/http")
    def http_route() -> None:
        raise HTTPException(status_code=404, detail="SQL table secret should never leave the server")

    @app.get("/unauthorized")
    def unauthorized_route() -> None:
        raise HTTPException(
            status_code=401,
            detail="authentication internals",
            headers={
                "WWW-Authenticate": 'Bearer realm="api"',
                "Content-Type": "text/plain",
            },
        )

    @app.get("/forbidden")
    def forbidden_route() -> None:
        raise HTTPException(status_code=403, detail="authorization internals")

    @app.get("/rate-limited")
    def rate_limited_route() -> None:
        raise HTTPException(
            status_code=429,
            detail="limiter internals",
            headers={"Retry-After": "30"},
        )

    @app.get("/http-204")
    def http_204_route() -> None:
        raise HTTPException(status_code=204, detail="not an error response")

    @app.get("/integrity")
    def integrity_route(request: Request) -> None:
        request.state.session = session
        raise IntegrityError(
            "INSERT INTO secret_table",
            {"context": "database-value"},
            RuntimeError("secret_constraint_name"),
        )

    @app.get("/unexpected")
    def unexpected_route() -> None:
        raise RuntimeError("stack and SQL secret")

    return app


def test_request_validation_returns_one_localized_error_per_problem_with_escaped_pointers() -> None:
    client = TestClient(_create_handler_test_app(RollbackSession()))

    response = client.post(
        "/validation",
        headers={"Accept-Language": "en-US"},
        json={"data": {"a/b~c": "wrong", "items": [{"id": "wrong"}]}},
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.headers["vary"] == "Accept-Language"
    assert len(response.json()["errors"]) == 2
    assert {error["source"]["pointer"] for error in response.json()["errors"]} == {
        "/data/a~1b~0c",
        "/data/items/0/id",
    }
    assert {error["code"] for error in response.json()["errors"]} == {"VALIDATION_ERROR"}
    assert {error["status"] for error in response.json()["errors"]} == {"422"}


def test_validation_sources_distinguish_body_query_header_and_non_representable_inputs() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/sources/{resource_id}")
    def source_route(
        resource_id: int,
        document: ValidationDocument,
        score: int = Query(alias="filter[score]"),
        trace: int = Header(alias="X-Trace"),
        session_id: int = Cookie(alias="session_id"),
    ) -> dict[str, bool]:
        return {"ok": bool(resource_id + document.data.items[0].id + score + trace + session_id)}

    response = TestClient(app).post(
        "/sources/not-an-id?filter[score]=not-an-int",
        headers={"X-Trace": "not-an-int", "Cookie": "session_id=not-an-int"},
        json={"data": {"a/b~c": "wrong", "items": [{"id": "wrong"}]}},
    )
    errors = response.json()["errors"]

    assert response.status_code == 422
    assert {error.get("source", {}).get("pointer") for error in errors} >= {
        "/data/a~1b~0c",
        "/data/items/0/id",
    }
    assert {error.get("source", {}).get("parameter") for error in errors} >= {"filter[score]"}
    assert {error.get("source", {}).get("header") for error in errors} >= {"X-Trace"}
    assert sum("source" not in error for error in errors) == 2

    malformed_json = TestClient(app).post(
        "/sources/1?filter[score]=1",
        headers={"X-Trace": "1", "Cookie": "session_id=1", "Content-Type": "application/json"},
        content="{",
    )
    assert malformed_json.status_code == 422
    assert "source" not in malformed_json.json()["errors"][0]


def test_typed_exception_uses_localization_without_exposing_language_header() -> None:
    raw_language = "en-US, private-language-value;q=0.1"
    response = TestClient(_create_handler_test_app(RollbackSession())).get(
        "/typed",
        headers={"Accept-Language": raw_language},
    )

    assert response.status_code == 400
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.headers["vary"] == "Accept-Language"
    assert response.json()["errors"][0] == {
        "status": "400",
        "code": "INVALID_QUERY_PARAMETER",
        "title": "Invalid query parameter",
        "detail": "The filter value is invalid.",
        "source": {"parameter": "filter[score]"},
    }
    assert raw_language not in response.text
    assert "Accept-Language" not in response.text


def test_http_exception_maps_stable_code_and_does_not_expose_original_detail() -> None:
    response = TestClient(_create_handler_test_app(RollbackSession())).get(
        "/http",
        headers={"Accept-Language": "en"},
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.headers["vary"] == "Accept-Language"
    assert response.json()["errors"][0]["status"] == "404"
    assert response.json()["errors"][0]["code"] == "RESOURCE_NOT_FOUND"
    assert "SQL table secret" not in response.text


@pytest.mark.parametrize(
    ("path", "status_code", "header_name", "header_value"),
    [
        ("/unauthorized", 401, "www-authenticate", 'Bearer realm="api"'),
        ("/rate-limited", 429, "retry-after", "30"),
    ],
)
def test_generic_http_errors_preserve_safe_headers_and_original_status(
    path: str,
    status_code: int,
    header_name: str,
    header_value: str,
) -> None:
    response = TestClient(_create_handler_test_app(RollbackSession())).get(path)

    assert response.status_code == status_code
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.headers[header_name] == header_value
    assert response.json()["errors"][0]["status"] == str(status_code)
    assert response.json()["errors"][0]["code"] == "HTTP_ERROR"
    assert "internals" not in response.text


def test_http_exception_removes_headers_invalidated_by_jsonapi_body_rewrite() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/unsafe-headers")
    def unsafe_headers_route() -> None:
        raise HTTPException(
            status_code=409,
            headers={
                "Content-Type": "text/plain",
                "CONTENT-LENGTH": "1",
                "content-length": "2",
                "Transfer-Encoding": "chunked",
                "Trailer": "X-Checksum",
                "Keep-Alive": "timeout=5",
                "TE": "trailers",
                "Upgrade": "websocket",
                "Proxy-Connection": "keep-alive",
                "Content-Range": "bytes 0-1/2",
                "Content-Language": "fr",
                "Content-Disposition": "attachment; filename=error.txt",
                "ETag": '"stale"',
                "Last-Modified": "Tue, 14 Jul 2026 00:00:00 GMT",
                "Digest": "sha-256=stale",
                "Content-MD5": "stale",
                "Accept-Ranges": "bytes",
            },
        )

    response = TestClient(app).get("/unsafe-headers")
    removed_headers = {
        "transfer-encoding",
        "trailer",
        "keep-alive",
        "te",
        "upgrade",
        "proxy-connection",
        "content-range",
        "content-language",
        "content-disposition",
        "etag",
        "last-modified",
        "digest",
        "content-md5",
        "accept-ranges",
    }

    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.headers["content-length"] == str(len(response.content))
    assert removed_headers.isdisjoint(response.headers)
    assert response.json()["errors"][0]["code"] == "RESOURCE_CONFLICT"


def test_http_exception_removes_connection_and_its_nominated_headers_case_insensitively() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/connection-headers")
    def connection_headers_route() -> None:
        raise HTTPException(
            status_code=400,
            headers={
                "cOnNeCtIoN": " X-Remove, x-also-remove ",
                "X-REMOVE": "first",
                "x-remove": "second",
                "X-Also-Remove": "third",
                "X-Keep": "safe",
            },
        )

    response = TestClient(app).get("/connection-headers")

    assert "connection" not in response.headers
    assert "x-remove" not in response.headers
    assert "x-also-remove" not in response.headers
    assert response.headers["x-keep"] == "safe"


def test_http_exception_preserves_safe_response_headers() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/safe-headers")
    def safe_headers_route() -> None:
        raise HTTPException(
            status_code=401,
            headers={
                "Allow": "GET, HEAD",
                "WWW-Authenticate": 'Bearer realm="api"',
                "Retry-After": "30",
                "Location": "/login",
                "Cache-Control": "no-store",
                "vArY": "Origin",
                "Set-Cookie": "session=expired; Path=/; HttpOnly",
                "X-Request-ID": "request-1",
            },
        )

    response = TestClient(app).get("/safe-headers")

    assert response.headers["allow"] == "GET, HEAD"
    assert response.headers["www-authenticate"] == 'Bearer realm="api"'
    assert response.headers["retry-after"] == "30"
    assert response.headers["location"] == "/login"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["vary"] == "Origin, Accept-Language"
    assert response.headers["set-cookie"] == "session=expired; Path=/; HttpOnly"
    assert response.headers["x-request-id"] == "request-1"


def test_http_exception_merges_vary_tokens_case_insensitively_without_duplicates() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/vary")
    def vary_route() -> None:
        raise HTTPException(
            status_code=400,
            headers={"Vary": "Origin, aCcEpT-LaNgUaGe, ORIGIN"},
        )

    response = TestClient(app).get("/vary")
    vary_tokens = [token.strip().casefold() for token in response.headers["vary"].split(",")]

    assert vary_tokens == ["origin", "accept-language"]


def test_http_exception_with_stale_gzip_metadata_returns_parseable_jsonapi_document() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/stale-gzip")
    def stale_gzip_route() -> None:
        raise HTTPException(
            status_code=500,
            headers={
                "Content-Encoding": "gzip",
                "Content-Length": "20",
                "Transfer-Encoding": "chunked",
            },
        )

    response = TestClient(app).get("/stale-gzip")

    assert "content-encoding" not in response.headers
    assert "transfer-encoding" not in response.headers
    assert response.headers["content-length"] == str(len(response.content))
    assert response.json()["errors"][0]["code"] == "INTERNAL_SERVER_ERROR"


def test_unmapped_forbidden_http_error_uses_stable_generic_code() -> None:
    response = TestClient(_create_handler_test_app(RollbackSession())).get("/forbidden")

    assert response.status_code == 403
    assert response.json()["errors"][0]["status"] == "403"
    assert response.json()["errors"][0]["code"] == "HTTP_ERROR"
    assert "authorization internals" not in response.text


def test_http_exception_with_204_is_normalized_to_500_error_document() -> None:
    response = TestClient(
        _create_handler_test_app(RollbackSession()),
        raise_server_exceptions=False,
    ).get("/http-204")

    assert response.status_code == 500
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json()["errors"][0]["code"] == "INTERNAL_SERVER_ERROR"


def test_integrity_error_rolls_back_and_returns_safe_conflict() -> None:
    session = RollbackSession()
    response = TestClient(_create_handler_test_app(session)).get(
        "/integrity",
        headers={"Accept-Language": "en"},
    )

    assert session.rollback_calls == 1
    assert response.status_code == 409
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.headers["vary"] == "Accept-Language"
    assert response.json()["errors"][0]["code"] == "RESOURCE_CONFLICT"
    assert response.json()["errors"][0]["status"] == "409"
    assert "secret_table" not in response.text
    assert "secret_constraint_name" not in response.text
    assert "database-value" not in response.text


def test_unexpected_error_is_logged_and_returns_generic_500() -> None:
    with patch("app.jsonapi.exception_handlers.logger") as error_logger:
        response = TestClient(
            _create_handler_test_app(RollbackSession()),
            raise_server_exceptions=False,
        ).get("/unexpected", headers={"Accept-Language": "en"})

    assert response.status_code == 500
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.headers["vary"] == "Accept-Language"
    assert response.json()["errors"][0]["code"] == "INTERNAL_SERVER_ERROR"
    assert response.json()["errors"][0]["status"] == "500"
    assert "stack and SQL secret" not in response.text
    error_logger.exception.assert_called_once()
    assert isinstance(error_logger.exception.call_args.kwargs["exc_info"], RuntimeError)


def test_register_exception_handlers_uses_specific_to_general_order() -> None:
    app = FastAPI()

    with patch.object(app, "add_exception_handler", wraps=app.add_exception_handler) as add_handler:
        register_exception_handlers(app)

    assert [call.args[0] for call in add_handler.call_args_list] == [
        JsonApiException,
        RequestValidationError,
        HTTPException,
        IntegrityError,
        Exception,
    ]
    assert [call.args[1] for call in add_handler.call_args_list] == [
        handle_jsonapi_exception,
        handle_request_validation,
        handle_http_exception,
        handle_integrity_error,
        handle_unexpected_error,
    ]


def test_application_factory_registers_jsonapi_exception_handlers() -> None:
    app = create_app()

    assert app.exception_handlers[JsonApiException] is handle_jsonapi_exception
    assert app.exception_handlers[RequestValidationError] is handle_request_validation
    assert app.exception_handlers[HTTPException] is handle_http_exception
    assert app.exception_handlers[IntegrityError] is handle_integrity_error
    assert app.exception_handlers[Exception] is handle_unexpected_error


def test_application_factory_converts_router_404_and_405_to_jsonapi() -> None:
    client = TestClient(create_app())

    not_found = client.get("/missing", headers={"Accept-Language": "en"})
    method_not_allowed = client.post(
        "/api/v1/examples/00000000-0000-4000-8000-000000000001/category",
        headers={"Accept-Language": "en"},
    )

    for response in (not_found, method_not_allowed):
        assert response.status_code in {404, 405}
        assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
        assert response.json()["errors"][0]["status"] == str(response.status_code)
        assert "detail" in response.json()["errors"][0]

    assert not_found.json()["errors"][0]["code"] == "RESOURCE_NOT_FOUND"
    assert method_not_allowed.json()["errors"][0]["code"] == "HTTP_ERROR"
    assert method_not_allowed.headers["allow"] == "GET"

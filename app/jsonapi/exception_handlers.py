"""Central FastAPI exception conversion for JSON:API errors."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException
from starlette.types import HTTPExceptionHandler

from app.jsonapi.documents import ErrorDocument, ErrorObject
from app.jsonapi.errors import ErrorCode, JsonApiException
from app.jsonapi.localization import localize_error, resolve_language
from app.jsonapi.responses import JsonApiResponse

logger = logging.getLogger(__name__)

_HTTP_ERROR_CODES: dict[int, ErrorCode] = {
    400: "INVALID_JSONAPI_DOCUMENT",
    404: "RESOURCE_NOT_FOUND",
    406: "NOT_ACCEPTABLE",
    409: "RESOURCE_CONFLICT",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
}


def _vary_accept_language(headers: Mapping[str, str] | None) -> dict[str, str]:
    response_headers: dict[str, str] = {}
    vary_tokens: list[str] = []
    seen_vary_tokens: set[str] = set()

    for name, value in (headers or {}).items():
        if name.casefold() != "vary":
            response_headers[name] = value
            continue
        for raw_token in value.split(","):
            token = raw_token.strip()
            normalized_token = token.casefold()
            if token and normalized_token not in seen_vary_tokens:
                vary_tokens.append(token)
                seen_vary_tokens.add(normalized_token)

    if "*" in seen_vary_tokens:
        vary_tokens = ["*"]
    elif "accept-language" not in seen_vary_tokens:
        vary_tokens.append("Accept-Language")

    response_headers["Vary"] = ", ".join(vary_tokens)
    return response_headers


def _error_response(
    status_code: int,
    errors: list[ErrorObject],
    headers: Mapping[str, str] | None = None,
) -> JsonApiResponse:
    return JsonApiResponse(
        ErrorDocument(errors=errors),
        status_code=status_code,
        headers=_vary_accept_language(headers),
    )


def _pointer_segment(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _validation_source(
    problem: Mapping[str, Any],
) -> tuple[str | None, str | None, str | None]:
    location = problem.get("loc", ())
    if not isinstance(location, Sequence) or isinstance(location, str) or not location:
        return None, None, None

    location_type = location[0]
    if location_type == "body" and problem.get("type") != "json_invalid":
        segments = location[1:]
        if segments:
            pointer = "/" + "/".join(_pointer_segment(segment) for segment in segments)
            return pointer, None, None
    if location_type == "query" and len(location) > 1:
        return None, str(location[1]), None
    if location_type == "header" and len(location) > 1:
        return None, None, str(location[1])
    return None, None, None


def _http_error_status_and_code(status_code: int) -> tuple[int, ErrorCode]:
    if not 400 <= status_code <= 599:
        return 500, "INTERNAL_SERVER_ERROR"
    if status_code >= 500:
        return status_code, "INTERNAL_SERVER_ERROR"
    return status_code, _HTTP_ERROR_CODES.get(status_code, "HTTP_ERROR")


async def handle_jsonapi_exception(
    request: Request,
    exception: JsonApiException,
) -> JsonApiResponse:
    language = resolve_language(request.headers.get("accept-language"))
    error = localize_error(
        exception.code,
        language=language,
        status=exception.status_code,
        source_pointer=exception.source_pointer,
        source_parameter=exception.source_parameter,
        source_header=exception.source_header,
        detail_args=exception.detail_args,
        detail_override=exception.detail_override,
    )
    return _error_response(exception.status_code, [error])


async def handle_request_validation(
    request: Request,
    exception: RequestValidationError,
) -> JsonApiResponse:
    language = resolve_language(request.headers.get("accept-language"))
    errors: list[ErrorObject] = []
    for problem in exception.errors():
        source_pointer, source_parameter, source_header = _validation_source(problem)
        errors.append(
            localize_error(
                "VALIDATION_ERROR",
                language=language,
                status=422,
                source_pointer=source_pointer,
                source_parameter=source_parameter,
                source_header=source_header,
            )
        )
    if not errors:
        errors.append(localize_error("VALIDATION_ERROR", language=language, status=422))
    return _error_response(422, errors)


async def handle_http_exception(
    request: Request,
    exception: HTTPException,
) -> JsonApiResponse:
    status_code, code = _http_error_status_and_code(exception.status_code)
    error = localize_error(
        code,
        language=resolve_language(request.headers.get("accept-language")),
        status=status_code,
    )
    return _error_response(status_code, [error], headers=exception.headers)


async def handle_integrity_error(
    request: Request,
    exception: IntegrityError,
) -> JsonApiResponse:
    session: Any | None = getattr(request.state, "session", None)
    if session is not None:
        try:
            session.rollback()
        except Exception:
            logger.exception("Failed to roll back request session")

    error = localize_error(
        "RESOURCE_CONFLICT",
        language=resolve_language(request.headers.get("accept-language")),
        status=409,
    )
    return _error_response(409, [error])


async def handle_unexpected_error(
    request: Request,
    exception: Exception,
) -> JsonApiResponse:
    logger.exception("Unexpected API error", exc_info=exception)
    error = localize_error(
        "INTERNAL_SERVER_ERROR",
        language=resolve_language(request.headers.get("accept-language")),
        status=500,
    )
    return _error_response(500, [error])


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(JsonApiException, cast(HTTPExceptionHandler, handle_jsonapi_exception))
    app.add_exception_handler(RequestValidationError, cast(HTTPExceptionHandler, handle_request_validation))
    app.add_exception_handler(HTTPException, cast(HTTPExceptionHandler, handle_http_exception))
    app.add_exception_handler(IntegrityError, cast(HTTPExceptionHandler, handle_integrity_error))
    app.add_exception_handler(Exception, handle_unexpected_error)

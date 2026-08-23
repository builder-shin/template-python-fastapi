"""Starlette response and OpenAPI error-response declarations for JSON:API documents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from starlette.background import BackgroundTask
from starlette.responses import JSONResponse

from app.jsonapi.documents import ErrorDocument, JsonApiDocument, RelationshipDocument

JSONAPI_MEDIA_TYPE = "application/vnd.api+json"

ERROR_RESPONSE_DESCRIPTIONS = {
    400: "Invalid JSON:API request",
    401: "Authentication required",
    403: "Forbidden",
    404: "Resource not found",
    406: "Not acceptable",
    409: "Resource conflict",
    415: "Unsupported media type",
    422: "Validation error",
    500: "Internal server error",
    503: "Service unavailable",
}
"""Single OpenAPI description per error status, shared by every controller."""


def jsonapi_error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Build the OpenAPI ``responses`` mapping for JSON:API error status codes."""

    return {
        status_code: {
            "description": ERROR_RESPONSE_DESCRIPTIONS[status_code],
            "model": ErrorDocument,
        }
        for status_code in status_codes
    }


_UNSAFE_REWRITTEN_RESPONSE_HEADERS = frozenset(
    {
        "accept-ranges",
        "connection",
        "content-disposition",
        "content-encoding",
        "content-language",
        "content-length",
        "content-md5",
        "content-range",
        "content-type",
        "digest",
        "etag",
        "keep-alive",
        "last-modified",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


def _sanitize_rewritten_response_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}

    connection_headers = {
        token.strip().casefold()
        for key, value in headers.items()
        if key.casefold() == "connection"
        for token in value.split(",")
        if token.strip()
    }
    blocked_headers = _UNSAFE_REWRITTEN_RESPONSE_HEADERS | connection_headers
    return {key: value for key, value in headers.items() if key.casefold() not in blocked_headers}


class JsonApiResponse(JSONResponse):
    media_type = JSONAPI_MEDIA_TYPE

    def __init__(
        self,
        content: JsonApiDocument | RelationshipDocument,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        background: BackgroundTask | None = None,
    ) -> None:
        response_headers = _sanitize_rewritten_response_headers(headers)
        response_headers["Content-Type"] = JSONAPI_MEDIA_TYPE
        super().__init__(
            content=content,
            status_code=status_code,
            headers=response_headers,
            media_type=JSONAPI_MEDIA_TYPE,
            background=background,
        )

    def render(self, content: Any) -> bytes:
        """Serialize the JSON:API document directly to bytes without an intermediate dict.

        ``content`` is always a document instance because ``__init__`` accepts nothing else.
        """
        document: JsonApiDocument | RelationshipDocument = content
        return document.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")

"""Starlette response for JSON:API documents."""

from __future__ import annotations

from collections.abc import Mapping

from starlette.background import BackgroundTask
from starlette.responses import JSONResponse

from app.jsonapi.documents import JsonApiDocument, RelationshipDocument

JSONAPI_MEDIA_TYPE = "application/vnd.api+json"

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
            content=content.model_dump(mode="json", by_alias=True, exclude_none=True),
            status_code=status_code,
            headers=response_headers,
            media_type=JSONAPI_MEDIA_TYPE,
            background=background,
        )

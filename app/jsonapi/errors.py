"""Stable JSON:API error codes and safe typed exceptions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from app.jsonapi.documents import JsonScalar

type Language = Literal["ko", "en"]
type ErrorCode = Literal[
    "NOT_ACCEPTABLE",
    "UNSUPPORTED_MEDIA_TYPE",
    "INVALID_JSONAPI_DOCUMENT",
    "INVALID_QUERY_PARAMETER",
    "INVALID_FILTER",
    "INVALID_SORT",
    "INVALID_INCLUDE",
    "INVALID_PAGE",
    "RESOURCE_NOT_FOUND",
    "RELATIONSHIP_RESOURCE_NOT_FOUND",
    "TYPE_MISMATCH",
    "ID_MISMATCH",
    "CLIENT_GENERATED_ID_UNSUPPORTED",
    "RESOURCE_CONFLICT",
    "VALIDATION_ERROR",
    "INTERNAL_SERVER_ERROR",
    "HTTP_ERROR",
    "AUTHENTICATION_REQUIRED",
    "INVALID_CREDENTIALS",
    "INVALID_TOKEN",
    "TOKEN_EXPIRED",
    "TOKEN_REVOKED",
    "USER_INACTIVE",
    "EMAIL_ALREADY_REGISTERED",
]


@dataclass(frozen=True, slots=True)
class ErrorMessage:
    title: str
    detail: str


ERROR_CODES: tuple[ErrorCode, ...] = (
    "NOT_ACCEPTABLE",
    "UNSUPPORTED_MEDIA_TYPE",
    "INVALID_JSONAPI_DOCUMENT",
    "INVALID_QUERY_PARAMETER",
    "INVALID_FILTER",
    "INVALID_SORT",
    "INVALID_INCLUDE",
    "INVALID_PAGE",
    "RESOURCE_NOT_FOUND",
    "RELATIONSHIP_RESOURCE_NOT_FOUND",
    "TYPE_MISMATCH",
    "ID_MISMATCH",
    "CLIENT_GENERATED_ID_UNSUPPORTED",
    "RESOURCE_CONFLICT",
    "VALIDATION_ERROR",
    "INTERNAL_SERVER_ERROR",
    "HTTP_ERROR",
    "AUTHENTICATION_REQUIRED",
    "INVALID_CREDENTIALS",
    "INVALID_TOKEN",
    "TOKEN_EXPIRED",
    "TOKEN_REVOKED",
    "USER_INACTIVE",
    "EMAIL_ALREADY_REGISTERED",
)

ERROR_CATALOG: dict[Language, dict[ErrorCode, ErrorMessage]] = {
    "ko": {
        "AUTHENTICATION_REQUIRED": ErrorMessage(
            title="인증 필요",
            detail="이 요청에는 인증이 필요합니다.",
        ),
        "EMAIL_ALREADY_REGISTERED": ErrorMessage(
            title="이미 등록된 이메일",
            detail="이미 등록된 이메일입니다.",
        ),
        "INVALID_CREDENTIALS": ErrorMessage(
            title="잘못된 인증 정보",
            detail="이메일 또는 비밀번호가 올바르지 않습니다.",
        ),
        "INVALID_TOKEN": ErrorMessage(
            title="유효하지 않은 토큰",
            detail="인증 토큰이 유효하지 않습니다.",
        ),
        "TOKEN_EXPIRED": ErrorMessage(
            title="만료된 토큰",
            detail="인증 토큰이 만료되었습니다.",
        ),
        "TOKEN_REVOKED": ErrorMessage(
            title="폐기된 토큰",
            detail="인증 토큰이 폐기되었습니다.",
        ),
        "USER_INACTIVE": ErrorMessage(
            title="비활성 사용자",
            detail="사용자 계정이 비활성 상태입니다.",
        ),
        "NOT_ACCEPTABLE": ErrorMessage(
            title="허용할 수 없는 응답 형식",
            detail="요청한 응답 형식을 지원하지 않습니다.",
        ),
        "UNSUPPORTED_MEDIA_TYPE": ErrorMessage(
            title="지원하지 않는 미디어 타입",
            detail="요청 본문은 JSON:API 미디어 타입을 사용해야 합니다.",
        ),
        "INVALID_JSONAPI_DOCUMENT": ErrorMessage(
            title="유효하지 않은 JSON:API 문서",
            detail="요청 문서가 JSON:API 형식에 맞지 않습니다.",
        ),
        "INVALID_QUERY_PARAMETER": ErrorMessage(
            title="유효하지 않은 쿼리 매개변수",
            detail="지원하지 않거나 잘못된 쿼리 매개변수입니다.",
        ),
        "INVALID_FILTER": ErrorMessage(
            title="유효하지 않은 필터",
            detail="지원하지 않거나 잘못된 필터입니다.",
        ),
        "INVALID_SORT": ErrorMessage(
            title="유효하지 않은 정렬",
            detail="지원하지 않거나 잘못된 정렬 항목입니다.",
        ),
        "INVALID_INCLUDE": ErrorMessage(
            title="유효하지 않은 포함 경로",
            detail="지원하지 않거나 잘못된 포함 경로입니다.",
        ),
        "INVALID_PAGE": ErrorMessage(
            title="유효하지 않은 페이지",
            detail="페이지 매개변수가 허용 범위를 벗어났습니다.",
        ),
        "RESOURCE_NOT_FOUND": ErrorMessage(
            title="리소스를 찾을 수 없음",
            detail="요청한 리소스를 찾을 수 없습니다.",
        ),
        "RELATIONSHIP_RESOURCE_NOT_FOUND": ErrorMessage(
            title="관계 리소스를 찾을 수 없음",
            detail="요청한 관계 리소스를 찾을 수 없습니다.",
        ),
        "TYPE_MISMATCH": ErrorMessage(
            title="리소스 타입 불일치",
            detail="요청한 리소스 타입이 대상 타입과 일치하지 않습니다.",
        ),
        "ID_MISMATCH": ErrorMessage(
            title="리소스 ID 불일치",
            detail="문서의 리소스 ID가 요청 경로와 일치하지 않습니다.",
        ),
        "CLIENT_GENERATED_ID_UNSUPPORTED": ErrorMessage(
            title="클라이언트 생성 ID 미지원",
            detail="이 생성 엔드포인트는 클라이언트가 지정한 리소스 ID를 지원하지 않습니다.",
        ),
        "RESOURCE_CONFLICT": ErrorMessage(
            title="리소스 충돌",
            detail="현재 리소스 상태와 요청이 충돌합니다.",
        ),
        "VALIDATION_ERROR": ErrorMessage(
            title="유효하지 않은 요청",
            detail="요청 값이 유효성 검사를 통과하지 못했습니다.",
        ),
        "INTERNAL_SERVER_ERROR": ErrorMessage(
            title="서버 내부 오류",
            detail="요청을 처리하는 중 서버 오류가 발생했습니다.",
        ),
        "HTTP_ERROR": ErrorMessage(
            title="HTTP 요청 오류",
            detail="HTTP 요청을 처리할 수 없습니다.",
        ),
    },
    "en": {
        "AUTHENTICATION_REQUIRED": ErrorMessage(
            title="Authentication required",
            detail="Authentication is required for this request.",
        ),
        "EMAIL_ALREADY_REGISTERED": ErrorMessage(
            title="Email already registered",
            detail="The email address is already registered.",
        ),
        "INVALID_CREDENTIALS": ErrorMessage(
            title="Invalid credentials",
            detail="The email or password is incorrect.",
        ),
        "INVALID_TOKEN": ErrorMessage(
            title="Invalid token",
            detail="The authentication token is invalid.",
        ),
        "TOKEN_EXPIRED": ErrorMessage(
            title="Token expired",
            detail="The authentication token has expired.",
        ),
        "TOKEN_REVOKED": ErrorMessage(
            title="Token revoked",
            detail="The authentication token has been revoked.",
        ),
        "USER_INACTIVE": ErrorMessage(
            title="User inactive",
            detail="The user account is inactive.",
        ),
        "NOT_ACCEPTABLE": ErrorMessage(
            title="Not acceptable",
            detail="The requested response format is not supported.",
        ),
        "UNSUPPORTED_MEDIA_TYPE": ErrorMessage(
            title="Unsupported media type",
            detail="The request body must use the JSON:API media type.",
        ),
        "INVALID_JSONAPI_DOCUMENT": ErrorMessage(
            title="Invalid JSON:API document",
            detail="The request document does not conform to JSON:API.",
        ),
        "INVALID_QUERY_PARAMETER": ErrorMessage(
            title="Invalid query parameter",
            detail="A query parameter is unsupported or invalid.",
        ),
        "INVALID_FILTER": ErrorMessage(
            title="Invalid filter",
            detail="A filter is unsupported or invalid.",
        ),
        "INVALID_SORT": ErrorMessage(
            title="Invalid sort",
            detail="A sort field is unsupported or invalid.",
        ),
        "INVALID_INCLUDE": ErrorMessage(
            title="Invalid include path",
            detail="An include path is unsupported or invalid.",
        ),
        "INVALID_PAGE": ErrorMessage(
            title="Invalid page",
            detail="A page parameter is outside the allowed range.",
        ),
        "RESOURCE_NOT_FOUND": ErrorMessage(
            title="Resource not found",
            detail="The requested resource could not be found.",
        ),
        "RELATIONSHIP_RESOURCE_NOT_FOUND": ErrorMessage(
            title="Relationship resource not found",
            detail="The requested relationship resource could not be found.",
        ),
        "TYPE_MISMATCH": ErrorMessage(
            title="Resource type mismatch",
            detail="The resource type does not match the target type.",
        ),
        "ID_MISMATCH": ErrorMessage(
            title="Resource ID mismatch",
            detail="The document resource ID does not match the request path.",
        ),
        "CLIENT_GENERATED_ID_UNSUPPORTED": ErrorMessage(
            title="Client-generated ID unsupported",
            detail="This create endpoint does not support a client-generated resource ID.",
        ),
        "RESOURCE_CONFLICT": ErrorMessage(
            title="Resource conflict",
            detail="The request conflicts with the current resource state.",
        ),
        "VALIDATION_ERROR": ErrorMessage(
            title="Invalid request",
            detail="A request value failed validation.",
        ),
        "INTERNAL_SERVER_ERROR": ErrorMessage(
            title="Internal server error",
            detail="The server encountered an error while processing the request.",
        ),
        "HTTP_ERROR": ErrorMessage(
            title="HTTP request error",
            detail="The HTTP request could not be processed.",
        ),
    },
}


class JsonApiException(Exception):  # noqa: N818 - public interface uses the framework term Exception
    """An intentional API error containing only response-safe information."""

    def __init__(
        self,
        *,
        status_code: int,
        code: ErrorCode,
        source_pointer: str | None = None,
        source_parameter: str | None = None,
        source_header: str | None = None,
        detail_args: Mapping[str, JsonScalar] | None = None,
        detail_override: str | None = None,
    ) -> None:
        if not 400 <= status_code <= 599:
            raise ValueError("JsonApiException status_code must be between 400 and 599")
        if detail_override is not None and ("\r" in detail_override or "\n" in detail_override):
            raise ValueError("detail_override must be a single safe line")

        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.source_pointer = source_pointer
        self.source_parameter = source_parameter
        self.source_header = source_header
        self.detail_args = dict(detail_args or {})
        self.detail_override = detail_override

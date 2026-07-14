# JSON:API 프로토콜 코어 지침

`app/jsonapi/`는 API 전체가 공유하는 JSON:API 1.1 문서, media type 협상, 조회 파싱, 오류 변환과 응답 형식의 단일 경계다. 자원별 비즈니스 규칙은 이곳이 아니라 schema·serializer·controller에 둔다.

## 모듈별 책임

| 모듈 | 책임 |
| --- | --- |
| `documents.py` | JSON:API 1.1 success/error/relationship 문서와 top-level 불변조건 |
| `negotiation.py` | `Accept`, 쓰기 `Content-Type`, vendor media type parameter 검증 |
| `query.py` | allowlisted filter/sort/include/page 파싱, SQLAlchemy 적용, pagination links |
| `errors.py` | 안전한 `JsonApiException`, 오류 코드와 한·영 메시지 catalog |
| `localization.py` | `Accept-Language` 협상과 localized `ErrorObject` 생성 |
| `exception_handlers.py` | FastAPI·Pydantic·SQLAlchemy 예외를 JSON:API 오류 문서로 변환 |
| `responses.py` | `application/vnd.api+json` 응답과 재작성하면 안 되는 header 보호 |

## 불변 계약

- 모든 성공 문서는 `jsonapi.version = "1.1"`을 포함하고, 최상위 문서에는 `data`와 `errors`를 함께 넣지 않는다. `included`는 `data`가 있는 문서에서만 사용한다.
- API media type은 정확히 `application/vnd.api+json`이다. 쓰기 `Content-Type`은 `profile` 외 parameter를 허용하지 않고, `Accept`는 vendor type·`application/*`·`*/*`의 q 값 규칙으로 판정한다.
- query parser는 `filter[...]`, 단일 `sort`, 단일 `include`, `page[number]`, `page[size]`만 받는다. policy에 없는 filter 연산자·정렬·include 경로, 중복 parameter, 지원하지 않는 query parameter는 오류로 반환한다.
- `fields[...]` 희소 필드셋은 지원하지 않는다. serializer attributes를 요청별로 동적으로 바꾸거나 query parser에 예외를 추가하지 않는다.
- filter 값은 policy의 parser로 변환하고 `contains`는 autoescape된 SQLAlchemy 표현을 사용한다. 정렬에는 policy tie breaker를 붙여 페이지 순서를 결정적으로 유지한다.

## 오류와 언어

- 외부에 보이는 오류는 `JsonApiException` 또는 `register_exception_handlers` 경로를 거친 `ErrorDocument`여야 한다. traceback, DB 제약 메시지, FastAPI 기본 detail을 노출하지 않는다.
- handler는 validation 위치를 JSON Pointer 또는 query/header source로 변환하고, `IntegrityError`는 409 `RESOURCE_CONFLICT`로 바꾸며 request session rollback을 시도한다.
- 오류 언어는 `Accept-Language`로 `ko` 또는 `en`을 선택하고, header 값이 없거나 유효하지 않으면 한국어가 기본이다. localized 응답은 `Vary: Accept-Language`를 보존한다.
- 새 오류 코드는 `ErrorCode` type alias, `ERROR_CODES`, 한국어·영어 `ERROR_CATALOG`, `tests/jsonapi/test_errors.py`의 parity 기대값을 한 변경으로 갱신한다. status mapping을 바꾸면 해당 exception handler 회귀도 함께 갱신하며, 한 언어만 추가하거나 controller에서 번역 문자열을 직접 만들지 않는다.

## 변경 확인

- 문서 모델: `uv run pytest --no-cov tests/jsonapi/test_documents.py -q`
- 협상과 응답: `uv run pytest --no-cov tests/jsonapi/test_negotiation.py tests/jsonapi/test_responses.py -q`
- query와 오류: `uv run pytest --no-cov tests/jsonapi/test_query.py tests/jsonapi/test_errors.py -q`

프로토콜 변경은 위 테스트와 controller 통합 응답을 함께 확인한다. 새 JSON:API 동작을 특정 route의 ad-hoc response로 구현하지 않는다.

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
| `naming.py` | 공개 camelCase 이름 규칙(`snake_to_camel`)과 strict write 입력 base |
| `responses.py` | `application/vnd.api+json` 응답, 재작성하면 안 되는 header 보호, 공용 OpenAPI 오류 응답 description 표 |

## 불변 계약

- 모든 성공 문서는 `jsonapi.version = "1.1"`을 포함하고, 최상위 문서에는 `data`와 `errors`를 함께 넣지 않는다. `included`는 `data`가 있는 문서에서만 사용한다.
- API media type은 정확히 `application/vnd.api+json`이다. 쓰기 `Content-Type`은 `profile` 외 parameter를 허용하지 않고, `Accept`는 vendor type·`application/*`·`*/*`의 q 값 규칙으로 판정한다.
- query parser는 `filter[...]`, 단일 `sort`, 단일 `include`, 그리고 `page[number]`·`page[size]`·`page[totals]`·`page[after]`·`page[before]` 다섯 개의 page member만 받는다. policy에 없는 filter 연산자·정렬·include 경로, 중복 parameter, 지원하지 않는 query parameter는 오류로 반환한다.
- keyset `WHERE`는 선두 정렬 컬럼의 비엄격 경계(`<=`/`>=`)를 `OR` 분기와 함께 `AND`로 묶어 sargable하게 유지한다. 이 경계는 모든 분기가 이미 함의하므로 결과는 바뀌지 않지만, 빼면 PostgreSQL이 인덱스를 처음부터 읽어 cursor 앞 행을 전부 filter로 버리므로 deep OFFSET과 같은 비용이 된다.
- 총 건수는 opt-in이다. `page[totals]=true`가 없으면 COLLECTION 응답은 COUNT 쿼리를 실행하지 않고 `meta.totalCount`를 넣지 않으며 `links.last`는 `null`이다. `next`는 총계가 아니라 `apply_pagination(..., probe=True)`가 한 행 더 읽어서 판정한다. COUNT를 이미 지불한 요청에서는 probe와 총계를 OR로 합쳐, `next: null`과 더 뒤쪽 `last`가 같은 문서에 함께 나오지 않게 한다. `page[totals]`는 모든 pagination 링크에 다시 실려 opt-in이 링크를 따라가도 유지된다.
- `page[after]`/`page[before]`는 keyset cursor다. cursor는 요청의 유효 정렬(정렬 이름·방향 서명)에 묶이고 `QueryPolicy.sorts`+tie breaker로만 해석되며, 정렬 컬럼은 NOT NULL이면서 cursor codec이 표현할 수 있는 타입이어야 한다. 두 조건 중 하나라도 어긋나면 parse 시점에 `INVALID_PAGE`로 거부한다. nullable만 검사하면 codec이 표현하지 못하는 정렬이 진입점 cursor를 통과해 `next`를 영영 만들 수 없는 첫 페이지를 내보내고, 손으로 만든 같은 정렬의 cursor는 decode에서 400이 된다. 빈 값은 컬렉션의 시작·끝을 뜻하는 진입점이다. 손상된 cursor, 정렬이 바뀐 cursor, `page[after]`+`page[before]`, cursor+`page[number]` 조합은 모두 `INVALID_PAGE`로 거부한다. 새 오류 코드는 만들지 않는다.
- 공개 attribute·relationship 이름은 `naming.py`의 `snake_to_camel` 하나로만 만든다. 요청 스키마는 `JsonApiWriteSchema`(또는 `create_model`용 `WRITE_MODEL_CONFIG`)를 상속해 camelCase alias·`extra="forbid"`·`strict=True`를 함께 받는다. serializer나 controller에 두 번째 변환 함수나 별도 `ConfigDict`를 만들지 않는다.
- strict base 때문에 JSON 문자열로 오는 `StrEnum` 속성은 `Field(strict=False)`가 필요하다. FastAPI는 body를 `validate_python`으로 검증하므로, 이 예외를 지우면 해당 자원의 모든 쓰기가 422가 된다.
- 응답 본문은 `JsonApiResponse.render`가 `model_dump_json(by_alias=True, exclude_none=True)` 한 번으로 만든다. 중간 dict를 거쳐 `json.dumps`로 다시 직렬화하지 않는다. 그래서 float 표기는 `json.dumps`의 `repr`이 아니라 pydantic-core를 따른다. 절대값이 `1e-4`보다 작은 attribute는 `1e-05`·`1e-07`이 아니라 `0.00001`·`1e-7`로 나가 바이트 길이와 `Content-Length`가 달라진다. decode된 값은 동일하지만 이 변경 이전에 저장한 byte 스냅샷은 갱신해야 한다. 이 wire 계약은 `tests/jsonapi/test_responses.py`가 구현을 다시 유도하지 않는 literal byte로 고정한다.

- OpenAPI 오류 응답은 `jsonapi_error_responses(*status_codes)`로 선언한다. description 문자열을 controller마다 새로 쓰지 말고 `ERROR_RESPONSE_DESCRIPTIONS`에 status별로 한 번만 정의한다.
- `fields[...]` 희소 필드셋은 지원하지 않는다. serializer attributes를 요청별로 동적으로 바꾸거나 query parser에 예외를 추가하지 않는다.
- filter 값은 policy의 parser로 변환하고 `contains`는 autoescape된 SQLAlchemy 표현을 사용한다. 정렬에는 policy tie breaker를 붙여 페이지 순서를 결정적으로 유지한다.

## 오류와 언어

- 외부에 보이는 오류는 `JsonApiException` 또는 `register_exception_handlers` 경로를 거친 `ErrorDocument`여야 한다. traceback, DB 제약 메시지, FastAPI 기본 detail을 노출하지 않는다.
- handler는 validation 위치를 JSON Pointer 또는 query/header source로 변환하고, `IntegrityError`는 409 `RESOURCE_CONFLICT`로 바꾸며 request session rollback을 시도한다.
- 오류 언어는 `Accept-Language`로 `ko` 또는 `en`을 선택하고, header 값이 없거나 유효하지 않으면 한국어가 기본이다. localized 응답은 `Vary: Accept-Language`를 보존한다.
- 새 오류 코드는 `ErrorCode` type alias와 `_CATALOG` 항목 두 곳만 갱신한다. `_CATALOG` 항목은 `LocalizedErrorMessage(ko=..., en=...)`로 한국어·영어 메시지를 함께 담고, `ERROR_CODES`와 `ERROR_CATALOG`는 여기서 파생한다. mypy가 `ErrorCode`에 없는 코드와 빠진 언어를 거부하고, `tests/jsonapi/test_errors.py`가 Literal↔catalog parity를 검증한다. status mapping을 바꾸면 해당 exception handler 회귀도 함께 갱신하며, 한 언어만 추가하거나 controller에서 번역 문자열을 직접 만들지 않는다.

## 변경 확인

- 문서 모델: `uv run pytest --no-cov tests/jsonapi/test_documents.py -q`
- 협상과 응답: `uv run pytest --no-cov tests/jsonapi/test_negotiation.py tests/jsonapi/test_responses.py -q`
- query와 오류: `uv run pytest --no-cov tests/jsonapi/test_query.py tests/jsonapi/test_errors.py -q`

프로토콜 변경은 위 테스트와 controller 통합 응답을 함께 확인한다. 새 JSON:API 동작을 특정 route의 ad-hoc response로 구현하지 않는다.

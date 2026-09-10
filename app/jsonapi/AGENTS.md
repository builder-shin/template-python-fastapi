<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# JSON:API 프로토콜 코어 지침

## Purpose

JSON:API 1.1 문서, media type 협상, query 파싱, 오류 변환과 응답 형식의 공통 경계다. 자원별 비즈니스 규칙은 schema·serializer·controller에 둔다.

## Key Files

| 파일 | 역할 |
| --- | --- |
| `__init__.py` | 문서·응답·오류·협상 public export |
| `documents.py` | success/error/relationship 문서, top-level 불변조건, 명시적 null 보존 |
| `negotiation.py` | Accept·body Content-Type과 media type parameter·q 값 검증 |
| `query.py` | allowlisted filter/sort/include/page 파싱, SQL 적용, pagination link |
| `errors.py` | 안전한 JsonApiException, ErrorCode·LocalizedErrorMessage catalog와 파생 공개 표 |
| `localization.py` | Accept-Language 협상과 localized ErrorObject |
| `exception_handlers.py` | FastAPI·Pydantic·SQLAlchemy 예외를 안전한 JSON:API 오류로 변환 |
| `naming.py` | snake_to_camel·JsonApiWriteSchema·WRITE_MODEL_CONFIG의 공통 입력/이름 규칙 |
| `responses.py` | Pydantic 직접 byte 응답·header 보호·공용 OpenAPI 오류 response 선언 |

## For AI Agents

### Working In This Directory

- 모든 문서는 `jsonapi.version="1.1"`을 포함한다. top-level data와 errors를 함께 넣지 않고, data/errors/meta 중 하나가 있어야 하며 included는 data가 있는 문서에만 둔다.
- MISSING과 명시적 null을 구분한다. `exclude_none=True` 직렬화에서도 요청한 `data: null`은 문서와 관계에서 유지하고 생략된 data는 만들지 않는다.
- 오류 문서는 비어 있지 않은 errors를 가지며 ErrorObject와 RelationshipObject의 최소 구성 조건을 유지한다. arbitrary extra member·비유한 수를 허용하지 않는다.
- media type은 `application/vnd.api+json`이다. body Content-Type은 profile 외 parameter를 거부하고, Accept는 vendor·application/*·*/*의 구체성·q 값으로 판정한다. 명시적 q=0을 넓은 wildcard로 뒤집지 않는다.
- 프로토콜을 특정 route의 ad-hoc 응답으로 구현하거나 JSON:API 응답을 일반 JSON으로 바꾸지 않는다.

- 공개 attribute·relationship 이름은 naming.py의 snake_to_camel 하나로 만든다. 요청 schema는 JsonApiWriteSchema, 동적 create_model은 WRITE_MODEL_CONFIG로 camelCase·extra 금지·strict 설정을 공유한다.
- strict base에서 JSON 문자열 StrEnum은 해당 필드에 Field(strict=False)가 필요하다. FastAPI validate_python 경로의 예외를 제거해 모든 정상 쓰기를 422로 만들지 않는다.
- OpenAPI 오류는 jsonapi_error_responses(*status_codes)를 사용하고 status별 description은 ERROR_RESPONSE_DESCRIPTIONS에서만 선언한다.

### Query 계약

- collection parser는 filter[...]·단일 sort·단일 include와 page[number]·page[size]·page[totals]·page[after]·page[before]만 받는다. policy 밖 연산자·sort·include, 중복·미지원 parameter는 오류다. fields[...] 희소 필드셋을 추가하지 않는다.
- resource GET은 include만, to-many related URL의 parse_page_query는 page[number]·page[size]만 허용한다. to-one related와 linkage·쓰기는 controller concern에서 query를 거부한다.
- filter는 policy parser로 변환한다. contains는 autoescape=True, in은 비어 있지 않은 값 목록, isNull은 정확한 true/false 문자열이다. sort에는 결정적 tie breaker를 붙인다.
- offset page는 기본 1·20개, 최대 크기 100이며 양수·SQL offset 범위를 검증한다. 총계는 collection index의 page[totals]=true로 선택한다. 기본 index는 COUNT·meta.totalCount 없이 size+1 probe로 next를 판단하고 offset links.last는 null이다.
- 총계가 있으면 offset next는 probe와 총계 결과를 OR로 합쳐 next=null과 더 뒤쪽 last가 공존하지 않게 한다. page[totals]=true는 후속 pagination 링크에도 보존한다.
- page[after]·page[before]는 유효 정렬 이름·방향의 서명과 값을 가진 keyset cursor다. 빈 값은 시작·끝 진입점이다. 손상·정렬 불일치·after+before·cursor+page[number] 조합은 INVALID_PAGE다.
- cursor는 QueryPolicy.sorts와 tie breaker로만 해석한다. 모든 정렬 column은 NOT NULL이고 codec이 왕복 표현할 수 있는 타입이어야 한다. nullable·미지원 타입은 빈 진입점 cursor라도 parse 단계에서 거부한다.
- keyset WHERE는 혼합 ASC/DESC를 OR-of-AND로 표현하고 선두 정렬 column의 비엄격 <=/>= 경계를 AND로 더해 인덱스 시작 조건을 유지한다. 이 조건을 빼 deep OFFSET과 같은 앞행 스캔 비용을 만들지 않는다.
- keyset에는 OFFSET을 적용하지 않는다. before는 DB 정렬을 반전하고 controller가 결과를 다시 뒤집는다. cursor 링크의 first·last는 totals와 무관하게 빈 after·before 경계로 만든다.
- pagination 링크는 path 기준 상대 URL이고 다른 query 조건을 보존한다. 잘못된 total·overflow를 느슨하게 처리하지 않는다.

### 오류·언어·Header

- 외부 오류는 JsonApiException 또는 등록된 handler를 통해 ErrorDocument로 반환한다. traceback, DB 제약 메시지, FastAPI 원본 detail을 노출하지 않는다.
- validation 위치를 JSON Pointer 또는 query/header source로 옮기고 pointer의 ~와 /를 escape한다. IntegrityError는 request session rollback을 시도한 후 409 RESOURCE_CONFLICT로 바꾼다.
- Accept-Language는 ko/en을 선택하며 header가 없거나 유효하지 않으면 한국어가 기본이다. localized 응답의 Vary에 Accept-Language를 병합하고 기존 *를 보존한다.
- 새 코드는 ErrorCode type alias와 `_CATALOG` 항목 두 곳을 갱신한다. 항목은 `LocalizedErrorMessage(ko=..., en=...)`로 두 언어를 함께 담고 ERROR_CODES·ERROR_CATALOG는 여기서 파생한다. mypy와 `tests/jsonapi/test_errors.py`의 Literal↔catalog parity를 함께 확인한다. status mapping 변경은 handler 회귀도 갱신한다. controller에서 번역 문자열을 직접 만들지 않는다.
- 응답은 JsonApiResponse.render에서 `model_dump_json(by_alias=True, exclude_none=True)`를 한 번 호출해 UTF-8 bytes로 만든다. 중간 dict·json.dumps로 재직렬화하지 않는다. float wire 표기는 pydantic-core 기준이며 예를 들어 `0.00001`·`1e-7`로 출력되어 이전 json.dumps byte snapshot·Content-Length와 다를 수 있다. 이 계약은 test_responses.py의 literal byte 기대값으로 검증한다.
- JsonApiResponse는 문서의 camelCase·명시적 null 계약을 유지한다. 재작성된 body에 맞지 않는 Content-Length·Content-Encoding·ETag 등과 hop-by-hop·Connection 지정 header를 전달하지 않고 Content-Type을 고정한다.

### Testing Requirements

`TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리킬 때만 아래 좁은 pytest를 실행한다. URL이 없으면 `./scripts/check.sh`의 임시 Docker PostgreSQL 전체 게이트를 사용한다.

| 변경 | 확인 |
| --- | --- |
| 문서 | `uv run pytest --no-cov tests/jsonapi/test_documents.py -q` |
| 협상·응답·공통 이름 | `uv run pytest --no-cov tests/jsonapi/test_negotiation.py tests/jsonapi/test_responses.py tests/jsonapi/test_naming.py -q` |
| query·오류 | `uv run pytest --no-cov tests/jsonapi/test_query.py tests/jsonapi/test_errors.py -q` |

프로토콜 변경은 controller 통합 응답과 함께 확인하고 최종 API 게이트는 루트 지침을 따른다.

### Common Patterns

문서는 Pydantic 모델, policy·query spec은 불변 dataclass, 예상 오류는 안정된 코드와 source를 가진 JsonApiException으로 표현한다. 순수 parser는 자원 allowlist를 전달받고 SQL은 명시된 SQLAlchemy column만 사용한다.

## Dependencies

- 내부: [schemas](../schemas/AGENTS.md)가 제공하는 QueryPolicy, [controller](../controllers/AGENTS.md)·[serializer](../serializers/AGENTS.md)의 문서 사용, [config](../../config/AGENTS.md)의 전역 handler 등록.
- 외부: Pydantic, FastAPI/Starlette, SQLAlchemy; urllib.parse·re 표준 라이브러리.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->

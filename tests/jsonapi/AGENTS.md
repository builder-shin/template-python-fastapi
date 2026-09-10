<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# JSON:API 프로토콜 테스트 지침

## 목적과 주요 파일

JSON:API 1.1의 문서·협상·오류·query·응답 계약을 검증한다. 순수 parser/model 검증, 최소 FastAPI 앱의 wire 응답, 실제 PostgreSQL query 실행을 해당 경계에 맞춰 사용한다.

| 파일 | 역할 |
| --- | --- |
| `test_documents.py` | 누락·null·Sentinel 구분, document 조합, JSON schema와 recursive JSON 값 |
| `test_negotiation.py` | Accept specificity·quality 문법, vendor Content-Type·profile·ext와 header dependency |
| `test_errors.py` | ko/en catalog·언어 선택, 오류 source·pointer escape, rollback·header 재작성·handler 등록 |
| `test_query.py` | 불변 allowlist·타입 변환, offset/keyset·totals·probe, cursor codec·거부 query·상대 링크 |
| `test_responses.py` | vendor media type·명시적 null·alias·OpenAPI, compact JSON과 부동소수점 wire bytes |
| `test_naming.py` | 단일 snake_to_camel 함수와 serializer·route·strict 쓰기 schema의 alias 설정 |

## 문서 모델과 schema

- 문서 모델 검증과 JSON schema 검증을 함께 유지한다. 한쪽 통과만으로 protocol 적합성을 주장하지 않는다.
- `data: null`, `data` 누락, `MISSING`과 오류 문서는 서로 다른 경우이므로 예외적으로 합치지 않는다.
- resource, relationship linkage, `included`, error document의 허용·필수 조합을 정상과 거부 요청으로 짝지어 검증한다.
- `included`의 `data` 종속성, 부적절한 null, data와 errors의 충돌은 문서 검증과 해당 공개 오류 계약으로 단언한다. 관계 linkage와 요청한 included resource는 구별한다.
- schema 실패는 status뿐 아니라 JSON:API 오류의 code와 source를 확인해 원인을 드러낸다.
- `snake_to_camel` 구현은 `app/jsonapi/naming.py` 하나를 공유해야 한다. `JsonApiWriteSchema`의 strict·extra forbid·populate_by_name과 auth·Example schema의 상속을 함께 확인한다.
- 응답 byte 기대값은 구현으로 다시 생성하지 않고 literal로 둔다. `model_dump_json(by_alias=True, exclude_none=True)`의 compact·비ASCII 원문·명시적 관계 null과 `pydantic-core`의 `0.00001`·`1e-7` 숫자 표기를 고정한다.

## 협상과 오류 출처

- `Accept` 테스트는 vendor media type의 specificity와 `q` 값을 교차해, 선택된 표현과 거부 결과를 명확히 단언한다.
- `Content-Type`의 허용 profile 문법과 거부하는 ext·charset·잘못된 매개변수를 분리하고, 다른 media type의 느슨한 수용을 추가하지 않는다.
- 한국어·영어 오류 catalog는 `ErrorCode` Literal을 기준으로 누락 없이 같은 code와 source 구조, 각 언어에 맞는 상세 문구를 모두 확인한다. 별도 수동 code 목록으로 새 오류를 놓치지 않는다.
- body pointer와 query parameter source, header source는 서로 바꾸어 단언하지 않는다.
- header rewrite와 exception handler 순서는 실제 응답의 status·header·오류 문서로 회귀를 고정한다.

## query와 링크

- 허용 query는 parser 결과뿐 아니라 route 응답을 검증하고, 거부 query는 원 요청 parameter를 `source.parameter`에 보존한다.
- allowlist 밖 filter, sort, include, page, `fields[...]`가 통과하지 않는 사례를 유지한다.
- wildcard filter·include를 편의상 주입하거나, 거부 입력을 조용히 삭제하는 기대값을 만들지 않는다.
- 정렬은 동점 상황에서도 결정적 순서를, pagination은 문서와 links의 일관된 경계를 단언한다.
- pagination links는 허용된 원 query를 보존하되, 잘못된 값을 정상 link로 되살리지 않는지 확인한다.
- `PageSpec`은 기본 totals false·cursor 없음이다. `page[totals]`는 true/false만 허용하며, probe는 size+1을 조회한다. total이 없을 때 offset last는 없고 next는 실제 probe로 결정한다.
- 빈 after는 처음, 빈 before는 마지막 경계다. cursor의 base64 JSON payload는 현재 sort 서명과 값 개수·타입을 검증한다. after/before 동시 사용·중복·number 혼용, nullable·미지원 sort 타입을 경계 cursor에서도 거부한다.
- bool·정수·실수·Decimal·날짜·시각·문자열·UUID·enum의 cursor round trip을 검증한다. 테스트용 codec 모델은 DB 테이블을 생성하지 않는다.
- cursor links는 전진·후진과 마지막의 완전한 window를 표현하는 빈 before 경계를 확인한다. 제한된 related-route parser인 `parse_page_query`는 number·size 외 query를 거부한다.

## 작성과 실행

- 하나의 테스트는 문서 불변조건, 협상, query, 오류 localization 중 하나의 주된 실패 원인을 드러내도록 좁힌다.
- status code만 검사하는 protocol 테스트는 추가하지 않는다. 필요한 header와 document 모양을 함께 확인한다.
- 예외 메시지 문자열보다 안정적인 code·source를 우선하고, 언어별 문구는 catalog 계약일 때만 정확히 고정한다.
- 새 query 문법은 수용, 거부, link 재현을 한 묶음으로 보강한다.
- 실행 명령: `uv run pytest --no-cov tests/jsonapi -q`. `TEST_DATABASE_URL`은 독립된 `*_test` PostgreSQL이어야 한다. URL이 없으면 `./scripts/check.sh`의 임시 Docker PostgreSQL 전체 게이트를 사용한다.

## 공통 패턴과 의존성

- 거부 입력은 `pytest.mark.parametrize`로 원본 parameter/header와 함께 고정한다. `test_query.py`의 테스트용 policy는 운영 `EXAMPLE_QUERY_POLICY`와 범위가 다르므로 운영 allowlist로 복사하지 않는다.
- SQL injection 모양의 값은 bind parameter로 남는지, `contains`의 `%`·`_`는 리터럴로 처리되는지 확인한다. 정렬 동점 fixture에는 고정 UUID·시각을 사용한다.
- pagination helper 자체는 중복 non-page query를 보존한다. 공개 route의 parser 거부 검증을 helper의 동작과 혼동하지 않는다.
- 오류 본문 재작성 시 stale encoding·length·hop-by-hop header 제거, 안전한 header 유지와 `Vary: Accept-Language` 병합을 함께 검토한다.
- 내부: `app/jsonapi`, Example ORM·serializer, 공유 앱·PostgreSQL fixture. 외부: pytest, Pydantic·pydantic-core, FastAPI·Starlette·TestClient, SQLAlchemy.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->

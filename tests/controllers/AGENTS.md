<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# Controller 회귀 테스트 지침

## 목적과 주요 파일

`JsonApiController`의 공통 라우터와 `CrudActions` concern의 선언형 확장점, JSON:API HTTP 계약, PostgreSQL 쓰기의 원자성을 검증한다. 실제 공개 자원 조립과 인증은 상위 디렉터리의 endpoint 통합 테스트가 담당한다.

| 파일 | 역할 |
| --- | --- |
| `test_crud_actions.py` | lifecycle hook·scope·dependency, 읽기 전용 route·OpenAPI, offset/cursor·opt-in totals와 SQL 비용, CRUD rollback |
| `test_jsonapi_controller.py` | prefix 검증·root 허용, JsonApiRoute 사용, Accept 생략 옵션과 독립적인 쓰기 Content-Type 검증 |
| `test_relationship_actions.py` | 관계 조회·제한된 pagination·중첩 대상 로딩, mutation·부모 행 잠금과 중복 추가의 멱등성 |
| `test_upsert.py` | PUT 생성·교체·SQL 비용, hook의 mapped field·FK·관계 저장, 외부 INSERT 경쟁과 remote-side FK |

## 대상과 진입점

- 이 디렉터리의 네 파일은 공통 controller concern의 route 기반 FastAPI 회귀를 둔다. `test_crud_actions.py`·`test_relationship_actions.py`·`test_upsert.py`는 `CrudActions`의 CRUD·관계·`PUT` 계약을, `test_jsonapi_controller.py`는 `JsonApiController`의 라우터 조립(prefix 검증, `Accept` 협상, 쓰기 `Content-Type`) 계약을 검증한다. 내부 action을 직접 호출해 HTTP 계약을 대신하지 않는다.
- 공통 concern 회귀는 `conftest.py`의 `minimal_app_factory`로 필요한 controller router와 exception handler만 등록한 최소 `FastAPI` 앱을 만든다. `FastAPI()` + `register_exception_handlers()` + `get_session` override를 직접 반복하지 않는다. handler 없는 앱이 필요하면 `register_handlers=False`, 세션을 직접 넘겨야 하면 `session_factory=`를 쓴다. 새 공개 자원의 factory·명시 route·OpenAPI 검증은 형제 `tests/test_<resource>_controller.py`에서 conftest `app`/`client` fixture로 수행한다.
- 인증을 포함한 앱 계측은 공유 `app_factory`의 `session_override`·`auth_session_factory_override`로 수행한다. 인증용 factory와 CRUD 세션은 각각 override하며, 인증 조회 세션은 endpoint 실행 전에 닫혀야 한다. `get_request_session`이 `request.state.session`에 결합한 CRUD 세션을 오류 handler가 rollback하는 경계를 확인한다.
- 요청은 자원이 노출한 vendor media type으로 보내고, 성공과 실패 모두 응답 `Content-Type`을 단언한다.
- 오류 응답은 status만 보지 말고 JSON:API `errors`의 `code`, `source`와 필요한 `pointer` 또는 `parameter`를 확인한다.
- 생성과 upsert 생성에서는 `Location`을, 204 응답에서는 빈 본문과 관련 header의 부재를 확인한다.

## CRUD와 관계 행위

- `enable_writes=False`는 쓰기 schema 없이도 조립되고, 관계 schema가 있어도 모든 mutation route를 제거한다. 기본 비활성인 upsert의 PUT은 405이며, 인증 OpenAPI 응답·security는 선언된 dependency가 있을 때만 노출한다.
- to-one 변경은 null, 올바른 linkage, 잘못된 type·id, 없는 대상을 각각 route로 검증한다.
- to-many 변경은 추가·제거·전체 대체를 분리하고, 중복 add가 관계를 중복시키지 않는 idempotent 결과를 단언한다.
- 관계 mutation 성공은 204 빈 응답과 commit 뒤 재조회한 관계 상태를 함께 검증한다.
- 관계 reset은 이전 linkage가 남지 않으며 새 문서의 linkage만 저장되는지 확인한다.
- 같은 관계 추가를 병렬로 요청할 때는 하나의 linkage만 남는지와 각 요청의 계약상 응답을 확인한다.
- to-many related route는 ID 정렬과 `meta.totalCount`, `page[number]`·`page[size]`만 허용하며 size는 100으로 제한한다. sort·filter·include·fields 등은 거부하고, to-one related route는 모든 query를 거부한다. 대상 serializer의 중첩 관계도 미리 로딩되는지 확인한다.

## 목록과 query 비용

- 기본 목록은 COUNT 없이 size+1 행으로 다음 페이지 존재를 확인한다. `page[totals]=true`일 때만 totalCount와 last를 제공하는 offset 계약을 검사한다.
- cursor 순회는 전진·후진의 순서·경계·중복 없는 전체 도달을 제한된 반복 횟수로 확인한다. keyset SQL에 OFFSET이나 불필요한 COUNT가 생기지 않아야 한다.
- join scope가 행을 증식시키면 ORM unique 처리 전 probe 결과로 다음 페이지를 판단해야 한다. 중복 행 때문에 남은 자원이 건너뛰어지지 않는 회귀를 유지한다.
- 잘못되거나 중복된 cursor, after/before 동시 사용, number와 cursor 혼용, 잘못된 totals, 지원하지 않는 nullable sort를 원 query parameter 오류로 확인한다.

## PUT과 원자성

- 새 ID `PUT`은 201과 `Location`, 기존 ID replace는 200과 갱신된 공개 표현을 단언한다.
- 같은 UUID로 동시 `PUT`을 실행할 때 단일 resource만 남고, 최종 표현이 유효한지 확인한다.
- lifecycle hook 실패와 serializer 실패는 각각 별도 회귀로 작성하고, 실패 뒤 committed DB에 생성·갱신·관계 변경이 남지 않음을 확인한다.
- 실패 경로도 vendor media type, JSON:API 오류 code·source를 단언해 handler 우회를 발견한다.
- DB 관찰은 요청 직후 같은 세션의 우연한 상태가 아니라 새 조회로 수행한다.
- 관계 없는 PUT 생성의 세 SQL(advisory lock·단일 선조회·ON CONFLICT RETURNING), 관계 포함 생성과 교체의 query 수를 실제 SQL로 고정한다. 불필요한 역방향 collection 로딩을 허용하지 않는다.
- 선조회 후 외부 INSERT가 끼어들어도 최초 생성 판단에 따른 201 계약을 유지한다. ON CONFLICT의 실제 insert/update 분기가 필요한 fallback 재조회를 결정하며 기존 linkage를 보존하는지 확인한다.
- remote-side FK 사례의 전용 mapped table은 별도 테스트 Base에만 만들고 `finally`에서 제거한다. production 모델의 migration 검증을 이 DDL로 대신하지 않는다.

## 경계와 실행

- controller fixture·helper는 route 문서와 최소 유효 payload를 읽기 쉽게 만들되, 공통 구현의 분기를 재현하지 않는다.
- 동시성 테스트는 barrier 또는 동등한 시작 지점을 사용해 실제 경쟁을 만들고, 순차 호출을 동시성으로 표기하지 않는다.
- 새 endpoint 회귀는 성공 하나와 가장 가까운 거부·rollback 경로를 함께 둔다.
- 실행 명령: `uv run pytest --no-cov tests/controllers -q`. 먼저 `TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리켜야 한다. URL이 없으면 `./scripts/check.sh`로 임시 Docker PostgreSQL을 사용하는 전체 게이트를 실행한다.
- 실패 원인은 요청 문서, 응답 계약, committed DB 상태 중 어느 단계인지 분리해 단언한다.

## 공통 패턴과 의존성

- 테스트용 controller는 모델·serializer·query policy와 활성화한 쓰기에 필요한 schema를 선언하고, 조사할 hook이나 dependency만 재정의한다. class 수준 hook 기록은 테스트 전에 초기화한다. None으로 바꾸는 관계 schema는 상위의 `ClassVar[type[BaseModel] | None]` 계약을 유지한다.
- serializer 실패를 유도할 때 FK만으로 linkage를 만들 수 있는 category 대신 실제 로딩이 필요한 tags 등을 사용한다.
- 불완전한 PUT attributes, 생략된 쓰기 관계의 reset, 쓰기 schema 밖 관계의 보존을 구별한다. 대문자 UUID 요청의 `Location`도 serializer의 정규 resource link와 일치해야 한다.
- 내부: `app/controllers/concerns`의 route 등록·문서 parsing·관계 resolver·upsert executor·CRUD 조립, `app/schemas/example.py`, `app/serializers`, `app/jsonapi`, `config/database.py`, 상위 앱·commit·동시성 fixture.
- 외부: FastAPI·TestClient, Pydantic, SQLAlchemy와 PostgreSQL, `ThreadPoolExecutor`·`Barrier`.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->

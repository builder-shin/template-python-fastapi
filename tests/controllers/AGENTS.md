# Controller 회귀 테스트 지침

## 대상과 진입점

- 이 디렉터리의 네 파일은 공통 controller concern의 route 기반 FastAPI 회귀를 둔다. `test_crud_actions.py`·`test_relationship_actions.py`·`test_upsert.py`는 `CrudActions`의 CRUD·관계·`PUT` 계약을, `test_jsonapi_controller.py`는 `JsonApiController`의 라우터 조립(prefix 검증, `Accept` 협상, 쓰기 `Content-Type`) 계약을 검증한다. 내부 action을 직접 호출해 HTTP 계약을 대신하지 않는다.
- 공통 concern 회귀는 `conftest.py`의 `minimal_app_factory`로 필요한 controller router와 exception handler만 등록한 최소 `FastAPI` 앱을 만든다. `FastAPI()` + `register_exception_handlers()` + `get_session` override를 직접 반복하지 않는다. handler 없는 앱이 필요하면 `register_handlers=False`, 세션을 직접 넘겨야 하면 `session_factory=`를 쓴다. 새 공개 자원의 factory·명시 route·OpenAPI 검증은 형제 `tests/test_<resource>_controller.py`에서 conftest `app`/`client` fixture로 수행한다.
- 요청은 자원이 노출한 vendor media type으로 보내고, 성공과 실패 모두 응답 `Content-Type`을 단언한다.
- 오류 응답은 status만 보지 말고 JSON:API `errors`의 `code`, `source`와 필요한 `pointer` 또는 `parameter`를 확인한다.
- 생성과 upsert 생성에서는 `Location`을, 204 응답에서는 빈 본문과 관련 header의 부재를 확인한다.

## CRUD와 관계 행위

- to-one 변경은 null, 올바른 linkage, 잘못된 type·id, 없는 대상을 각각 route로 검증한다.
- to-many 변경은 추가·제거·전체 대체를 분리하고, 중복 add가 관계를 중복시키지 않는 idempotent 결과를 단언한다.
- 관계 mutation 성공은 204 빈 응답과 commit 뒤 재조회한 관계 상태를 함께 검증한다.
- 관계 reset은 이전 linkage가 남지 않으며 새 문서의 linkage만 저장되는지 확인한다.
- 같은 관계 추가를 병렬로 요청할 때는 하나의 linkage만 남는지와 각 요청의 계약상 응답을 확인한다.

## PUT과 원자성

- 새 ID `PUT`은 201과 `Location`, 기존 ID replace는 200과 갱신된 공개 표현을 단언한다.
- 같은 UUID로 동시 `PUT`을 실행할 때 단일 resource만 남고, 최종 표현이 유효한지 확인한다.
- lifecycle hook 실패와 serializer 실패는 각각 별도 회귀로 작성하고, 실패 뒤 committed DB에 생성·갱신·관계 변경이 남지 않음을 확인한다.
- 실패 경로도 vendor media type, JSON:API 오류 code·source를 단언해 handler 우회를 발견한다.
- DB 관찰은 요청 직후 같은 세션의 우연한 상태가 아니라 새 조회로 수행한다.

## 경계와 실행

- controller fixture·helper는 route 문서와 최소 유효 payload를 읽기 쉽게 만들되, 공통 구현의 분기를 재현하지 않는다.
- 동시성 테스트는 barrier 또는 동등한 시작 지점을 사용해 실제 경쟁을 만들고, 순차 호출을 동시성으로 표기하지 않는다.
- 새 endpoint 회귀는 성공 하나와 가장 가까운 거부·rollback 경로를 함께 둔다.
- 실행 명령: `uv run pytest --no-cov tests/controllers -q`
- 실패 원인은 요청 문서, 응답 계약, committed DB 상태 중 어느 단계인지 분리해 단언한다.

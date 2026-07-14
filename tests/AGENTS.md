# API 테스트 지침

이 테스트는 SQLAlchemy 2와 PostgreSQL의 실제 동작을 검증한다. 메모리 SQLite, 모델 mock, DB 없는 controller 단위 테스트로 migration·관계·upsert 계약을 대체하지 않는다.

## fixture와 데이터베이스 경계

- `conftest.py`는 `TEST_DATABASE_URL`을 반드시 요구하고 database 이름이 `_test`로 끝나는지 확인한다. 테스트 대상이 아닌 DB URL을 우회하거나 fixture 검증을 약화하지 않는다.
- `migrated_database` fixture는 Alembic `head`까지 올린 DB를 제공한다. 새 schema 검증은 모델 metadata 생성 대신 migration을 통과한 DB에서 작성한다.
- `db_session`은 outer transaction과 savepoint를 사용해 각 테스트를 rollback한다. commit 관찰이 필요하면 `committed_session`, 동시성은 `concurrent_session_factory`를 사용하고 fixture 종료 후 truncate를 유지한다.
- 애플리케이션 통합 테스트는 `config.main:create_app`을 만들고 `get_session` dependency만 test engine으로 override한다. 프로덕션 route·exception handler·media type 등록을 건너뛰는 별도 app은 일반 자원 테스트에 쓰지 않는다.
- `tests/controllers/`의 공통 concern 회귀는 필요한 controller·handler만 등록한 최소 앱을 사용할 수 있다. 반면 새 공개 자원은 `tests/test_<resource>_controller.py`에서 반드시 `create_app()`으로 route 조립, OpenAPI, 대표 성공·거부 응답을 확인한다.
- 하위 경로를 빠르게 확인할 때는 `--no-cov`를 유지한다. 80% coverage gate는 전체 suite를 실행하는 `./scripts/check.sh`가 검증하므로, 부분 실행 결과를 전체 coverage 실패로 해석하지 않는다.

## 테스트 배치

세부 회귀 범위와 실행 명령은 각 하위 지침을 따른다.

| 경로 | 하위 지침 |
| --- | --- |
| `tests/controllers/` | `tests/controllers/AGENTS.md` |
| `tests/jsonapi/` | `tests/jsonapi/AGENTS.md` |
| `tests/serializers/` | `tests/serializers/AGENTS.md` |

| 경로 | 검증 대상 |
| --- | --- |
| `tests/jsonapi/` | 문서 불변조건, 협상, 오류, query parser, response header |
| `tests/controllers/` | `CrudActions`, 관계 mutation, PostgreSQL upsert와 rollback·동시성 |
| `tests/models/`, `tests/serializers/` | ORM 제약·관계와 공개 resource 표현 |
| `tests/config/`, `tests/integration/` | database 설정, migration 적용, 결정적 seed |
| `tests/test_example_controller.py` | 실제 factory, 명시 route, JSON:API wire 응답 |
| `tests/scripts/` | `scripts/check.sh`의 검증·정리 동작 |

## 회귀 테스트 작성 규칙

- HTTP 행위 변경은 status, `Content-Type`, JSON:API top-level 모양, 오류 code와 source를 함께 단언한다. `Accept-Language`를 건드리면 한국어와 영어 모두 확인한다.
- 새 query 허용 항목은 정상 요청과 거부 요청을 모두 작성한다. 허용되지 않은 filter/sort/include/page와 `fields[...]`가 통과하지 않는 회귀를 유지한다.
- 관계 변경은 to-one/to-many cardinality, linkage type·id 오류, 없는 대상, 204 mutation 응답, transaction rollback을 다룬다.
- `PUT` 변경은 create 201과 `Location`, replace 200, 동일 ID 동시 요청, 관계 reset, hook/직렬화 실패 rollback을 PostgreSQL에서 검증한다.
- migration은 upgrade 가능성과 seed 재실행의 결정성을 검증한다. 테스트가 특정 실행 순서나 개발 DB의 잔존 데이터에 의존하게 만들지 않는다.

## 실행 범위

- 빠른 protocol 확인: `uv run pytest --no-cov tests/jsonapi -q`
- 자원 endpoint 확인: `uv run pytest --no-cov tests/test_*_controller.py -q` (현재 `examples`의 기준 파일은 `tests/test_example_controller.py`다.)
- DB 경계 확인: `uv run pytest --no-cov tests/config tests/integration tests/test_database_fixtures.py -q`
- 전체 API 게이트: `./scripts/check.sh`

실패를 고칠 때는 같은 test subtree에서 재현 테스트를 먼저 보강하고, 마지막에 `./scripts/check.sh`로 실제 PostgreSQL 포함 전체 검증을 수행한다.

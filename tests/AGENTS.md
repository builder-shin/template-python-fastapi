# API 테스트 지침

이 테스트는 SQLAlchemy 2와 PostgreSQL의 실제 동작을 검증한다. 메모리 SQLite, 모델 mock, DB 없는 controller 단위 테스트로 migration·관계·upsert 계약을 대체하지 않는다.

## fixture와 데이터베이스 경계

- `conftest.py`는 `TEST_DATABASE_URL`을 반드시 요구하고 database 이름이 `_test`로 끝나는지 확인한다. 테스트 대상이 아닌 DB URL을 우회하거나 fixture 검증을 약화하지 않는다.
- `migrated_database` fixture는 Alembic `head`까지 올린 DB를 제공한다. 새 schema 검증은 모델 metadata 생성 대신 migration을 통과한 DB에서 작성한다.
- `db_session`은 outer transaction과 savepoint를 사용해 각 테스트를 rollback한다. commit 관찰이 필요하면 `committed_session`, 동시성은 `concurrent_session_factory`를 사용하고 fixture 종료 후 truncate를 유지한다.
- 애플리케이션 통합 테스트는 `conftest.py`의 `app`/`client` fixture를 사용한다. `create_app()`과 `dependency_overrides`를 직접 조립하지 않는다. 조립은 `conftest.py`의 `app_factory` 한 곳에만 존재한다.
- `app_factory`는 `get_session`과 `get_auth_session_factory`를 **둘 다** test engine으로 override한다. `get_session`은 endpoint 본문이, `get_auth_session_factory`는 bearer 조회가 사용하므로 한쪽만 override하면 인증과 쓰기가 서로 다른 engine을 보게 된다. 이 계약은 `tests/test_app_fixtures.py`가 고정한다.
- 세션을 관찰해야 하는 테스트는 조립을 복사하지 말고 `app_factory(session_override=..., auth_session_factory_override=...)`로 자기 generator만 넘긴다.
- `tests/controllers/`의 공통 concern 회귀는 `minimal_app_factory`로 필요한 router·handler만 등록한 최소 앱을 사용할 수 있다. 최소 앱은 bearer dependency가 없으므로 `get_session`만 override한다. 반면 새 공개 자원은 `tests/test_<resource>_controller.py`에서 반드시 `app`/`client` fixture로 route 조립, OpenAPI, 대표 성공·거부 응답을 확인한다.
- 예외: `tests/test_health_controller.py`는 liveness가 세션을 전혀 해석하지 않음을 단언하므로 모듈 자체 `app` fixture로 conftest fixture를 가려 DB 없는 앱을 유지한다.

| fixture | 언제 쓰나 |
| --- | --- |
| `app_factory` | 세션 override를 직접 지정해야 하는 계측 테스트 |
| `app` | 기본 override가 적용된 `create_app()` 애플리케이션 |
| `client` | 익명 `TestClient` (`raise_server_exceptions=False`) |
| `jsonapi_headers` | `Accept` + `Content-Type` vendor media type 헤더 (테스트마다 새 dict) |
| `auth_settings` | `app.state.auth_settings`. 토큰은 앱이 실제로 검증하는 설정으로 발급한다 |
| `persisted_user` | 사용자 commit factory (`email=`, `password=`, `is_active=`) |
| `access_token` | `access_token(application, user)` — 해당 애플리케이션 기준 access token |
| `authenticated_user` | `authenticated@example.com` 활성 사용자 |
| `authenticated_client` | bearer 헤더가 붙은 별도 `TestClient` (`client`는 익명으로 남는다) |
| `minimal_app_factory` | `minimal_app_factory(*routers, session_factory=..., register_handlers=...)` 최소 앱 |
- 하위 경로를 빠르게 확인할 때는 `--no-cov`를 유지한다. 80% coverage gate는 전체 suite를 실행하는 `./scripts/check.sh`가 검증하므로, 부분 실행 결과를 전체 coverage 실패로 해석하지 않는다.

## 타입 검사

- `tests/`도 `pyproject.toml`의 `[tool.mypy] strict = true` 검사 대상이다. `exclude`는 `[".venv/"]`뿐이며 `tests/`를 다시 제외하지 않는다.
- `db/`에는 `__init__.py`가 없다(`python -m db.seeds`와 hatch wheel packages 때문). 그래서 `explicit_package_bases = true`와 `mypy_path = "."`가 필수다. 둘 중 하나라도 빠지면 `uv run mypy .`가 `Source file found twice under different module names: "seeds" and "db.seeds"`로 검사 자체를 중단한다. `db/__init__.py`를 만들어 우회하지 않는다.
- `warn_unused_ignores`가 켜져 있으므로 `# type: ignore`는 반드시 오류 코드를 명시하고, 필요 없어진 주석은 mypy가 즉시 실패로 알린다.
- 잘못된 입력을 일부러 넣어 검증층의 거부를 확인하는 negative test는 해당 줄에 정확한 코드의 `# type: ignore[...]`로 의도를 남긴다. 타입을 맞추려고 단언을 바꾸지 않는다.
- 테스트 타입을 맞추려고 `app/`·`config/`의 시그니처나 `no_implicit_reexport`를 완화하지 않는다. 재export 모듈 속성 읽기, private pool 속성 관찰, generator `close()` 같은 것은 테스트 쪽 `cast`나 per-line ignore로 해결한다.
- `CrudActions` 서브클래스를 테스트에서 정의할 때 `relationships_schema`처럼 base가 `type[BaseModel] | None`로 선언한 속성은 테스트에서도 같은 애노테이션을 붙인다. 그래야 하위 클래스가 `None`으로 덮을 수 있고 훅 시그니처 변경이 mypy에서 잡힌다.
- 타입만 빠르게 확인할 때는 `uv run poe typecheck`(= `mypy .`)를 쓴다.

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
- 타입 계약 확인: `uv run poe typecheck`
- 전체 API 게이트: `./scripts/check.sh` (`uv run poe check`)

실패를 고칠 때는 같은 test subtree에서 재현 테스트를 먼저 보강하고, 마지막에 `./scripts/check.sh`로 실제 PostgreSQL 포함 전체 검증을 수행한다.

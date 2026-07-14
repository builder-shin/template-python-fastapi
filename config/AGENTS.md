# config 조립 지침

## 애플리케이션과 라우트

- ASGI 진입점은 `config/asgi.py`의 `config.main:create_app`이다.
- `create_app`에서는 JSON:API 예외 handler를 먼저 등록한 뒤 명시적 `api_router`를 포함한다. 이 순서를 바꾸지 않는다.
- OpenAPI 경로는 `/api/schema`, 문서는 `/api-docs`이며 `redoc`과 Swagger OAuth redirect는 노출하지 않는다. 문서 경로나 앱 제목을 바꿀 때는 실제 factory 응답을 함께 확인한다.
- `config/routes.py`는 controller 인스턴스를 만들고 각 controller의 `router`를 `api_router`에 명시적으로 포함한다. 자동 탐색이나 import 부수 효과로 공개 route를 등록하지 않는다.
- 새 controller는 이 파일에서 prefix·tags와 함께 한 번만 조립한다. controller가 `APIRouter`를 전역 router에 직접 포함하거나 `create_app`을 import하지 않는다.

## 동기식 세션 조립

- `database.py`는 import 시점에 module-level `engine`과 `SessionFactory`를 만든다. 설정은 `DATABASE_URL`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`에서 읽는다.
- `DATABASE_URL`이 없을 때만 로컬 PostgreSQL URL을 사용한다. test용 URL 선택과 Alembic URL 우선순위는 이 디렉터리가 아니라 `db/migrations/`의 책임이다.
- pool 경계는 `pool_size >= 1`, `max_overflow >= 0`, `pool_timeout > 0`을 유지하고, engine에는 `pool_pre_ping=True`를 둔다.
- 설정값은 `DatabaseSettings`에서 검증한다. 새 환경변수로 같은 pool 값을 우회하거나 요청마다 새 engine을 만들지 않는다.
- `get_session`은 요청 범위 `Session`을 yield한 뒤 닫는다. 이 경로에서 별도 commit 정책을 추가하지 않는다.
- commit·rollback의 소유자는 `CrudActions` 같은 호출 계층이다. dependency가 예외를 삼키거나 session을 재사용 가능한 전역 상태로 바꾸지 않는다.

## 변경 영향

- 예외 handler, route, factory 순서를 바꾸면 `tests/test_example_controller.py`에서 실제 `create_app`과 JSON:API 404/405 변환을 확인한다.
- database 설정 변경은 기본값, 환경 override, 잘못된 pool 경계, `get_session` close를 `tests/config/test_database.py`에서 모두 확인한다.
- import 시 생성되는 engine 때문에 설정 관련 테스트는 module-level 객체와 환경변수의 생성 시점을 명확히 분리한다. 개발 DB 연결을 테스트의 암묵적 전제로 삼지 않는다.

## 좁은 확인

```bash
uv run pytest --no-cov tests/config/test_database.py tests/test_example_controller.py -q
```

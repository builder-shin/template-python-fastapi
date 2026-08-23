# config 조립 지침

## 애플리케이션과 라우트

- ASGI 진입점은 `config/asgi.py`의 `config.main:create_app`이다.
- `create_app`에서는 JSON:API 예외 handler를 먼저 등록한 뒤 명시적 `api_router`를 포함한다. 이 순서를 바꾸지 않는다.
- OpenAPI 경로는 `/api/schema`, 문서는 `/api-docs`이며 `redoc`과 Swagger OAuth redirect는 노출하지 않는다. 문서 경로나 앱 제목을 바꿀 때는 실제 factory 응답을 함께 확인한다.
- `config/routes.py`는 controller 인스턴스를 만들고 각 controller의 `router`를 `api_router`에 명시적으로 포함한다. 자동 탐색이나 import 부수 효과로 공개 route를 등록하지 않는다.
- 새 controller는 이 파일에서 prefix·tags와 함께 한 번만 조립한다. controller가 `APIRouter`를 전역 router에 직접 포함하거나 `create_app`을 import하지 않는다.

## 환경 설정 로딩

- 새 설정값은 `config/settings.py`의 `require_env`/`read_int`로 읽는다. 필수 값은 fail-closed로 처리하고, 프로덕션 코드에 하드코딩된 기본 URL이나 자격증명을 두지 않는다.
- `require_env(variable)`는 값이 없거나 공백뿐이면 `"{variable} is required"`로 실패한다. `read_int(variable, default)`는 파싱 실패 시 `"{variable} must be an integer"`로 실패해 어떤 변수가 문제인지 항상 드러낸다.
- 로컬 편의용 기본값은 `.env.example`과 `docker-compose.yml`에만 둔다. `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET_KEY`에는 코드상의 암묵적 기본값이 없다.
- 설정 dataclass는 각자 모듈(`auth.py`, `database.py`)에 두고, 로딩 규칙만 `settings.py`에서 공유한다.

## 동기식 세션 조립

- `database.py`는 import 시점에 engine을 만들지 않는다. `config.database`를 import하는 것만으로는 어떤 engine도 생성되지 않아야 한다.
- API의 engine과 session factory는 `create_app()`에서 `DatabaseSettings.from_env()`로 만들어 `app.state.engine`, `app.state.session_factory`에 두고, lifespan 종료 시 `engine.dispose()`로 정리한다.
- `get_session`과 `get_auth_session_factory`는 `Request`를 받아 `request.app.state.session_factory`를 사용한다. 두 dependency의 이름과 모듈 경로는 테스트의 `dependency_overrides` 키이므로 바꾸지 않는다.
- FastAPI 애플리케이션이 없는 진입점(워커, `db.seeds` CLI)은 `get_session_factory()`를 명시적으로 호출한다. 이 함수는 첫 호출에서만 engine을 만들어 프로세스 단위로 캐시한다.
- 설정은 `DATABASE_URL`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`에서 읽으며 `DATABASE_URL`은 필수다. test용 URL 선택과 Alembic URL 우선순위는 이 디렉터리가 아니라 `db/migrations/`의 책임이다.
- pool 경계는 `pool_size >= 1`, `max_overflow >= 0`, `pool_timeout > 0`을 유지하고, engine에는 `pool_pre_ping=True`를 둔다.
- 설정값은 `DatabaseSettings`에서 검증한다. 새 환경변수로 같은 pool 값을 우회하거나 요청마다 새 engine을 만들지 않는다.
- `get_session`은 요청 범위 `Session`을 yield한 뒤 닫는다. 이 경로에서 별도 commit 정책을 추가하지 않는다.
- 쓰기 endpoint는 `get_request_session`을 주입받는다. 이 dependency는 `get_session`을 감싸 `request.state.session`만 바인딩하고 같은 session을 돌려주므로, `IntegrityError` handler의 rollback 대상이 endpoint마다 손으로 대입되지 않는다. `get_session` 자체에 바인딩을 넣지 않는 이유는 테스트의 `dependency_overrides[get_session]`가 계속 하위 dependency로 치환되어야 하기 때문이다.
- `get_auth_session_factory`는 `request.state.session`을 바인딩하지 않는다. 인증은 endpoint 본문보다 먼저 실행되므로 여기서 바인딩하면 인증 session이 쓰기 session을 가린다.
- 인증 조회는 session이 아니라 factory를 주입받는다. `get_auth_session_factory`는 session factory 자체를 돌려주고, `get_current_user`가 자기 본문 안에서 session을 열어 조회한 뒤 `expunge`하고 즉시 닫는다. generator dependency는 응답 이후에야 정리되므로 그대로 두면 인증된 요청이 커넥션 2개를 응답 끝까지 점유한다. 지금 형태에서는 인증 커넥션이 endpoint 실행 전에 pool로 반납되어 인증된 쓰기 요청도 동시 커넥션 1개만 쓴다.
- 인증 조회 session을 `get_session`의 `Session`과 합치거나 `begin_nested()`로 대체하지 않는다. auth SELECT가 autobegin한 transaction 때문에 `CrudActions`와 `auth_controller`의 `with session.begin()`이 `InvalidRequestError`로 실패한다(SQLAlchemy 2.0 확인). 인증 전용 engine이나 pool도 따로 만들지 않는다.
- `get_current_user`가 돌려주는 `User`는 detached 인스턴스다. 이미 적재된 column 값만 안전하므로 `UserSerializer`에 relationship을 추가하지 않는다(지연 로딩 시 `DetachedInstanceError`). 조기 반납을 위해 `session.rollback()`을 쓰지 않는다 — rollback은 적재된 인스턴스를 expire시켜 `get_current_active_user`의 `is_active` 접근이 다시 조회를 일으킨다.
- commit·rollback의 소유자는 `CrudActions` 같은 호출 계층이다. dependency가 예외를 삼키거나 session을 재사용 가능한 전역 상태로 바꾸지 않는다.

## 변경 영향

- 예외 handler, route, factory 순서를 바꾸면 `tests/test_example_controller.py`에서 실제 `create_app`과 JSON:API 404/405 변환을 확인한다.
- database 설정 변경은 기본값, 환경 override, 잘못된 pool 경계, `get_session` close를 `tests/config/test_database.py`에서 모두 확인한다.
- 인증 session의 수명을 바꾸면 요청당 pool 점유 수를 `tests/test_example_controller.py`의 checkout/checkin 계측 테스트로, detached `User` 직렬화와 인증 오류 코드 계약을 `tests/auth/test_dependencies.py`와 `tests/test_user_controller.py`로 확인한다.
- engine 생성 시점을 바꾸면 `config.database` import가 engine을 만들지 않는지, `create_app()`이 `app.state`를 채우고 lifespan에서 dispose하는지를 `tests/config/test_database.py`에서 확인한다. 개발 DB 연결을 테스트의 암묵적 전제로 삼지 않는다.
- 필수 환경변수를 추가·변경하면 `tests/config/test_settings.py`와 `.env.example`, `docker-compose.yml`, README의 설정 목록을 함께 갱신한다.

## 좁은 확인

```bash
uv run pytest --no-cov tests/config/test_database.py tests/test_example_controller.py -q
```

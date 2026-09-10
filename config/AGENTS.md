<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# config 조립 지침

## 목적

FastAPI 앱과 공개 라우트, 동기식 SQLAlchemy 세션, JWT·보존 기간 환경 설정, Dramatiq Redis broker를 조립한다. API의 앱별 engine 수명과 worker·CLI의 지연 초기화를 구분하며, 도메인 로직이나 별도 repository/service 계층을 두는 위치가 아니다.

## 주요 파일

| 파일 | 책임 |
| --- | --- |
| `asgi.py` | `create_app()`을 호출해 ASGI 객체 `application`을 공개한다. |
| `main.py` | 인증·DB 설정을 검증하고 앱 상태, engine·session factory, lifespan, JSON:API 예외 handler와 router를 조립한다. |
| `routes.py` | auth, examples, categories, tags, health, users controller를 변수로 만들고 router를 명시적으로 포함한다. |
| `database.py` | `DatabaseSettings`, engine·session factory 생성, worker·CLI용 지연 factory, 요청 세션·인증 factory dependency를 제공한다. |
| `auth.py` | `AuthSettings`, JWT에 의존하지 않는 `RefreshSessionRetentionSettings`, 앱 상태의 `get_auth_settings`를 제공한다. |
| `settings.py` | 필수 환경변수 `require_env`와 정수 파싱 `read_int`의 공통 오류 계약을 정의한다. |
| `broker.py` | 필수 `REDIS_URL`로 Redis broker를 만들고 Dramatiq에 등록하는 `configure_broker()`를 제공한다. |
| `__init__.py` | 빈 Python package 표식이다. 조립이나 초기화 부수 효과를 추가하지 않는다. |

문서화할 하위 소스 디렉터리는 없다.

## 작업 지침

### 애플리케이션과 라우트

- ASGI 실행 객체는 `config.asgi:application`, factory는 `config.main:create_app`이다. ASGI 모듈 import는 factory 호출을 포함한다.
- `create_app()`은 `AuthSettings.from_env()` → `DatabaseSettings.from_env()` → `build_engine()` 순서로 준비한다. 앱의 `auth_settings`·`database_settings`·`engine`·`session_factory`를 `app.state`에 저장한 뒤 JSON:API 예외 handler, 마지막으로 `api_router`를 등록한다. handler가 route 포함보다 먼저 준비되는 순서를 유지한다.
- 앱 제목은 `FastAPI Template`, 버전은 `0.1.0`이다. OpenAPI 경로는 `/api/schema`, Swagger 문서는 `/api-docs`이며 ReDoc과 Swagger OAuth redirect는 노출하지 않는다. 제목·문서 경로 변경은 실제 factory 응답까지 확인한다.
- `routes.py`는 아래 controller 인스턴스를 module-level 변수로 만든 뒤 표의 순서대로 router를 포함한다. 새 controller도 prefix·tags와 함께 여기서 한 번만 조립한다.

| 변수·controller | prefix·tags |
| --- | --- |
| `auth_controller` / `AuthController` | `/api/v1/auth`, `authentication`. |
| `examples_controller` / `ExamplesController` | `/api/v1/examples`, `examples`. |
| `example_categories_controller` / `ExampleCategoriesController` | `/api/v1/categories`, `example categories`. |
| `example_tags_controller` / `ExampleTagsController` | `/api/v1/tags`, `example tags`. |
| `health_controller` / `HealthController` | 별도 prefix 없이 `/health/live`·`/health/ready`, `health`. |
| `users_controller` / `UsersController` | `/api/v1/users`, `users`. |

- category·tag controller는 `enable_writes=False`인 공개 읽기 전용 참조 자원이다. 실제 등록 메서드는 GET뿐이며 create/update/replace schema를 선언하지 않는다.
- 자동 탐색, controller import 부수 효과, controller의 전역 router 직접 등록이나 `create_app` 역방향 import를 도입하지 않는다.
- `CrudActions` 인스턴스를 `include_router(...)` 안에 바로 생성하지 않는다. `tests/config/test_routes.py`가 module-level 변수에서 조립된 집합을 검사한다.
- serializer의 `resource_path`와 controller prefix를 맞추고, 쓰기 관계 alias가 serializer에 존재하도록 유지한다. type 이름과 읽기 전용 메서드도 route 선언 일관성 테스트에서 확인한다.
- 공개 resource 동작은 `CrudActions` 선언과 명시적 훅에 둔다. JSON:API 오류·미디어 타입·언어 계약을 유지하고, 앱 factory에서 seed·DDL·worker broker를 시작하지 않는다.

### 환경 설정 로딩

- 새 필수 설정과 정수 설정은 `settings.py`의 `require_env`·`read_int`로 읽는다. 필수 값은 fail-closed로 처리하며, 운영 코드에 하드코딩된 기본 URL이나 자격증명을 두지 않는다.
- `require_env(variable)`는 값이 없거나 공백뿐이면 `"{variable} is required"`로 실패하고, 존재하는 값은 원문 그대로 반환한다. `read_int(variable, default)`는 미설정 시 default를 쓰고 파싱 실패 시 `"{variable} must be an integer"`로 실패한다.
- 로컬 편의용 URL·자격증명 기본값은 `.env.example`과 `docker-compose.yml`에만 둔다. `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET_KEY`에는 코드상의 암묵적 기본값이 없다.
- 설정 dataclass는 `auth.py`·`database.py` 등 해당 모듈에 두고 환경 로딩 규칙만 `settings.py`에서 공유한다.

| 환경변수 | 기본값·검증 |
| --- | --- |
| `DATABASE_URL` | 필수. API와 worker·seed CLI의 DB 설정이 읽으며 빈 값·공백은 거부한다. |
| `DB_POOL_SIZE` | 정수 `5`, 최소 `1`. |
| `DB_MAX_OVERFLOW` | 정수 `10`, 최소 `0`. |
| `DB_POOL_TIMEOUT` | 정수 `30`, `0`보다 커야 한다. |
| `JWT_SECRET_KEY` | 필수이며 UTF-8 인코딩 기준 최소 32바이트다. |
| `JWT_ISSUER`, `JWT_AUDIENCE` | 각각 `template-python-fastapi`; 빈 문자열이나 공백만 있는 값은 거부한다. |
| `JWT_ACCESS_EXPIRES_SECONDS` | 정수 `900`, `0`보다 커야 한다. |
| `JWT_REFRESH_EXPIRES_SECONDS` | 정수 `2592000`, `0`보다 커야 한다. |
| `JWT_LEEWAY_SECONDS` | 정수 `0`, 음수는 거부한다. |
| `REFRESH_SESSION_RETENTION_SECONDS` | 정수 `604800`(7일), 최소 `0`. purge actor의 보존 기간이다. |
| `REDIS_URL` | `configure_broker()`에서 요구하며 빈 값·공백은 거부한다. |

- `AuthSettings.from_env()`의 잘못된 secret·issuer/audience·만료·leeway 값은 `ValueError`로 앱 생성을 중단한다. `get_auth_settings(request)`는 factory가 만든 앱 상태의 설정을 돌려주며 요청마다 환경을 다시 읽지 않는다.
- `RefreshSessionRetentionSettings`는 `AuthSettings`와 분리한다. purge worker가 사용하지 않는 `JWT_SECRET_KEY`까지 요구하지 않도록 보존 기간을 JWT 설정 안으로 합치지 않는다.
- 필수 환경변수를 추가·변경하면 `tests/config/test_settings.py`, `.env.example`, `docker-compose.yml`, README의 설정 목록을 함께 갱신한다.

### 동기식 engine과 세션 수명

- `config.database` import만으로 engine을 만들지 않는다. 기존 module-level `engine`·`SessionFactory`를 되살리지 않는다.
- API의 engine과 session factory는 `create_app()`에서 만들고 `app.state.engine`·`app.state.session_factory`에 둔다. `_lifespan`의 `finally`에서 `engine.dispose()`로 정리한다.
- FastAPI 앱이 없는 worker·`db.seeds` CLI는 `get_session_factory()`를 명시적으로 호출한다. 첫 호출에서 `DatabaseSettings.from_env()`와 `build_session_factory()`로 생성한 factory를 프로세스 단위로 캐시한다.
- 지연 factory는 `threading.Lock` 안팎에서 캐시를 재검사한다. 초기 동시 worker 요청이 engine·pool을 여러 개 만들지 않도록 이 생성 보호를 유지한다. 이미 생성된 factory는 환경 변경만으로 교체되지 않는다.
- `build_session_factory(settings)`는 새 engine에 `expire_on_commit=False`인 sessionmaker를 연결한다. API가 만드는 sessionmaker도 같은 옵션을 사용한다.
- `DATABASE_URL`은 필수이며 DB 설정은 `TEST_DATABASE_URL`을 읽지 않는다. 테스트 URL 선택과 Alembic 우선순위는 테스트 fixture 및 [migration 지침](../db/migrations/AGENTS.md)을 따른다.
- SQLAlchemy 2 동기식 `Session`과 PostgreSQL을 유지한다. 비동기 session이나 SQLite 호환 우회 경로를 추가하지 않는다.
- pool 경계는 `DatabaseSettings`에서 검증하고 `build_engine`은 `pool_pre_ping=True`를 유지한다. 같은 pool 값을 다른 환경변수로 우회하거나 요청마다 engine을 새로 만들지 않는다.
- `get_session(request)`은 앱의 session factory에서 요청 범위 세션을 열어 yield한 뒤 닫는다. dependency에 자동 commit·예외 삼키기·전역 session 공유를 추가하지 않는다. commit·rollback의 소유자는 `CrudActions`·인증 controller 같은 호출 계층이다.
- 쓰기 endpoint는 `get_request_session`을 주입받는다. 이 wrapper는 `get_session`이 준 동일 세션을 `request.state.session`에 바인딩해 `IntegrityError` handler의 rollback 대상으로 삼는다. endpoint별 직접 대입으로 복제하지 않는다.
- state 바인딩을 `get_session` 본문으로 옮기지 않는다. wrapper가 하위 `get_session`에 의존해야 테스트의 `dependency_overrides[get_session]`가 계속 작동한다.

### 짧은 인증 조회 세션

- `get_auth_session_factory(request)`는 앱의 session factory 자체를 반환한다. dependency를 resolve하는 동안 세션을 열지 않고 `request.state.session`도 바인딩하지 않는다. 인증 경로가 쓰기 세션의 rollback 대상을 가리지 않게 한다.
- `get_current_user`는 전달받은 factory로 자기 본문에서 별도 세션을 열고 사용자 조회 후 `expunge(user)`하고 즉시 닫는다. 인증 커넥션을 endpoint 실행 전에 pool로 돌려주므로 인증된 쓰기 요청도 동시에 커넥션 하나만 점유한다.
- 인증 세션을 응답 이후에 닫히는 generator dependency로 바꾸지 않는다. 인증 조회 세션과 `get_session`의 쓰기 세션을 합치거나 `begin_nested()`로 대체하지 않는다. 인증 SELECT의 autobegin transaction이 `with session.begin()`과 충돌한다. 인증 전용 engine·pool도 만들지 않는다.
- 반환된 `User`는 detached 상태다. 이미 적재한 column 값만 사용하며 `UserSerializer`에 relationship을 추가하지 않는다. 지연 로딩은 `DetachedInstanceError`를 일으킬 수 있다.
- 조기 커넥션 반납에 `session.rollback()`을 쓰지 않는다. rollback은 인스턴스를 expire시켜 `is_active` 같은 이미 읽은 값 접근이 다시 조회를 요구하게 한다.
- `get_session`과 `get_auth_session_factory`의 이름·모듈 경로는 테스트 dependency override의 키다. 이 경계를 유지한다.

### Worker broker 경계

- `configure_broker()`는 필수 `REDIS_URL`로 `RedisBroker`를 만들고 `dramatiq.set_broker`에 등록한 객체를 반환한다. 잘못된 환경값은 broker 생성·등록 전에 실패해야 한다.
- [app/jobs](../app/jobs/AGENTS.md)는 broker 설정 후 `process_example`·`purge_expired_refresh_sessions` actor를 import한다. API의 `create_app` 경로에 broker import를 추가하지 않는다.
- API factory, broker 등록, worker·CLI의 DB factory 최초 생성은 서로 다른 조립 시점이다. 테스트와 설정 변경에서 이 시점을 구분하고 개발 DB 연결을 암묵적 전제로 삼지 않는다.

## 검증

좁은 pytest 명령은 `TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리킬 때 실행한다. URL이 없으면 개발 DB로 대체하지 말고 `./scripts/check.sh`의 임시 Docker PostgreSQL 전체 게이트를 사용한다.

| 변경 범위 | 확인할 테스트 |
| --- | --- |
| 필수 환경값·정수 오류 계약 | `tests/config/test_settings.py` |
| Pool·import 무생성·지연 factory 동시 초기화·state·lifespan·세션 wrapper | `tests/config/test_database.py` |
| JWT 검증·앱 저장 순서·보존 기간의 JWT 독립성 | `tests/config/test_auth.py` |
| 필수 Redis URL·broker 등록·actor 순서·API import 분리 | `tests/config/test_broker.py` |
| 조립된 controller 집합·prefix·관계 alias·type·읽기 전용 경로 | `tests/config/test_routes.py` |
| 실제 factory·JSON:API 404/405·인증 요청 checkout/checkin 점유 수 | `tests/test_example_controller.py` |
| Detached 사용자·인증 오류·현재 사용자 직렬화 | `tests/auth/test_dependencies.py`, `tests/test_user_controller.py` |
| 공개 category·tag 경로 | `tests/integration/test_reference_resources.py` |

```bash
uv run pytest --no-cov tests/config tests/test_example_controller.py -q
```

- 인증 세션 수명을 바꾸면 checkout/checkin 계측과 detached 사용자 테스트를 함께 실행한다. 실제 앱 통합 테스트에서는 endpoint용 `get_session`과 인증용 `get_auth_session_factory` override를 구분한다.
- 공개 인증 경로 변경은 `tests/test_auth_controller.py`도 실행한다. engine 생성 시점 변경은 import 무생성, 앱 state, lifespan dispose를 검증한다.
- API 변경의 최종 게이트는 `uv sync --frozen` 후 `./scripts/check.sh`다. 컨테이너 설정만 변경했다면 `docker compose config --quiet`를 실행한다.

## 의존 관계

- 내부: [controller](../app/controllers/AGENTS.md)와 [JSON:API](../app/jsonapi/AGENTS.md)의 요청·오류 조립, [인증](../app/auth/AGENTS.md)의 짧은 조회 세션, [worker](../app/jobs/AGENTS.md)와 [seed](../db/AGENTS.md)의 지연 session factory.
- 외부: FastAPI, SQLAlchemy 2, psycopg/PostgreSQL, Dramatiq·Redis와 Python `threading`.
- 배포 연결: 루트 `docker-compose.yml`의 migrate·api·worker가 환경을 제공하고, 테스트 DB 설정은 `tests/conftest.py`와 `scripts/check.sh`가 담당한다.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->

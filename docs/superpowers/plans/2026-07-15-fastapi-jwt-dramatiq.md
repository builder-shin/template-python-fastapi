# FastAPI JWT 및 Dramatiq 구현 계획

> **에이전트 작업자 필수 하위 스킬:** 구현 시 `superpowers:subagent-driven-development`(권장) 또는 `superpowers:executing-plans`를 사용해 이 계획을 작업 단위로 실행한다. 진행 상태는 체크박스(`- [ ]`)로 추적한다.

**목표:** 기존 Example JSON:API 계약을 유지하면서 Argon2 비밀번호, 회전형 JWT refresh session, 활성 사용자 기반 쓰기 보호, Redis Dramatiq worker를 추가한다.

**아키텍처:** 동기식 SQLAlchemy Session과 PostgreSQL transaction을 계속 사용한다. 인증 설정·비밀번호·JWT·refresh 회전은 `app/auth/`의 작은 함수 모듈로 구성하고 별도 repository/service 계층은 만들지 않는다. Auth/User controller는 명시적 APIRouter로 JSON:API 문서를 처리하며, 공통 `CrudActions`에는 읽기·쓰기 dependency 조립점만 추가한다. Dramatiq broker는 jobs package가 import될 때 명시적으로 설정되고 API factory import는 Redis에 의존하지 않는다.

**기술 스택:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2 synchronous Session, PostgreSQL 18, Alembic, PyJWT HS256, pwdlib Argon2, Dramatiq RedisBroker, pytest, StubBroker, Docker Compose

## 전역 제약조건

- 승인된 설계 문서 `docs/superpowers/specs/2026-07-15-jwt-dramatiq-design.md`의 경로, token claim, 만료 시간, 오류 code를 변경하지 않는다.
- root 및 하위 `AGENTS.md`의 계층 소유권을 지킨다. 모델은 저장 구조, schema는 입력, serializer는 공개 출력, controller는 조립만 소유한다.
- 비동기 SQLAlchemy, SQLite 우회, repository/service class, 자동 route 탐색, 자동 seed, Example 자동 enqueue를 추가하지 않는다.
- 인증 및 migration 테스트는 실제 `_test` PostgreSQL에서 실행하고 actor broker 테스트만 StubBroker를 사용한다.
- 각 작업은 실패 테스트 → 예상 실패 확인 → 최소 구현 → 좁은 통과 확인 → 커밋 순서로 진행한다.
- 부분 pytest에는 `--no-cov`를 사용하고 마지막에 `./scripts/check.sh`로 strict mypy·Ruff·80% coverage·detect-secrets 전체 gate를 통과한다.

---

## 실행 전 PostgreSQL 테스트 환경

- [ ] 격리된 test Compose project를 임의의 빈 host port로 시작한다.

실행: `TEST_DB_PORT=0 docker compose -f docker-compose.test.yml -p template-python-fastapi-plan up -d --wait db`

예상: `fastapi_template_test` PostgreSQL이 임의의 localhost port에서 healthy.

- [ ] 계획을 실행하는 같은 shell에 fail-closed test URL을 설정한다.

실행: `TEST_DATABASE_ENDPOINT="$(docker compose -f docker-compose.test.yml -p template-python-fastapi-plan port db 5432)"`

예상: `127.0.0.1:` 뒤에 숫자 port가 붙은 endpoint를 현재 shell 변수에 저장.

실행: `export TEST_DATABASE_URL="postgresql+psycopg://fastapi:fastapi@127.0.0.1:${TEST_DATABASE_ENDPOINT##*:}/fastapi_template_test"` <!-- pragma: allowlist secret -->

예상: 현재 shell에 URL 설정. 이후 모든 부분 pytest와 Alembic 명령은 이 shell에서 실행한다.

실행: `uv run python -c 'import os; assert os.environ["TEST_DATABASE_URL"].endswith("/fastapi_template_test")'`

예상: exit 0.

---

### 작업 1: 인증 의존성과 fail-closed JWT 설정 추가

**파일:**
- 수정: `pyproject.toml`
- 수정: `uv.lock`
- 생성: `config/auth.py`
- 수정: `config/main.py`
- 수정: `tests/conftest.py`
- 생성: `tests/config/test_auth.py`

- [ ] **1.1 실패 테스트 작성**

`tests/config/test_auth.py`에 다음 계약을 작성한다.

- `JWT_SECRET_KEY` 누락 및 UTF-8 기준 32바이트 미만이면 `AuthSettings.from_env()`와 `create_app()` 실패
- 32바이트 이상 secret 수락, fallback secret 없음
- 기본 issuer/audience `template-python-fastapi`, access 900초, refresh 2,592,000초, leeway 0
- env override 반영
- issuer/audience blank, 만료 0 이하, leeway 음수 거부
- `create_app()`이 auth settings를 `app.state.auth_settings`에 저장한 후 route를 include

- [ ] **1.2 실패 확인**

실행: `uv run pytest --no-cov tests/config/test_auth.py -q`

예상: `config.auth`가 없어 collection 단계에서 실패한다.

- [ ] **1.3 최소 구현**

runtime dependency에 `PyJWT>=2.12,<3`, `pwdlib[argon2]>=0.2,<1`, `email-validator>=2.2,<3`, `dramatiq[redis]>=2.2,<3`를 추가하고 lockfile을 갱신한다.

`config/auth.py`에는 frozen slots dataclass를 둔다.

```python
@dataclass(frozen=True, slots=True)
class AuthSettings:
    secret_key: str
    issuer: str = "template-python-fastapi"
    audience: str = "template-python-fastapi"
    access_expires_seconds: int = 900
    refresh_expires_seconds: int = 2_592_000
    leeway_seconds: int = 0

    @classmethod
    def from_env(cls) -> "AuthSettings": ...
```

secret 길이는 `len(secret.encode("utf-8"))`로 검사한다. `get_auth_settings(request: Request)`는 `request.app.state.auth_settings`만 반환한다. `create_app()` 첫 줄에서 settings를 만들고 FastAPI instance에 저장한 뒤 예외 handler와 router를 등록한다. Redis 설정을 이 경로에서 읽거나 연결하지 않는다. 기존 factory 테스트가 fail-closed production 정책을 우회하지 않으면서 실행되도록 `tests/conftest.py` 최상단에서만 32바이트 이상의 명시적 test secret을 `setdefault`한다.

- [ ] **1.4 통과 확인**

실행: `uv run pytest --no-cov tests/config/test_auth.py -q`

예상: PASS.

- [ ] **1.5 커밋**

```bash
git add pyproject.toml uv.lock config/auth.py config/main.py tests/conftest.py tests/config/test_auth.py
git commit -m "feat: add fail-closed auth settings"
```

### 작업 2: Argon2와 엄격한 JWT primitive 구현

**파일:**
- 생성: `app/auth/__init__.py`
- 생성: `app/auth/passwords.py`
- 생성: `app/auth/tokens.py`
- 생성: `tests/auth/test_passwords.py`
- 생성: `tests/auth/test_tokens.py`

- [ ] **2.1 실패 테스트 작성**

password tests는 Argon2 hash prefix, correct/incorrect verify, 같은 원문을 두 번 hash했을 때 salt가 달라짐, dummy hash 검증 경로를 확인한다. token tests는 고정 clock/settings로 다음을 단언한다.

- access 900초, refresh 2,592,000초
- `sub`, UUID `jti`, `type`, timezone-aware `iat`/`exp`, `iss`, `aud` 모두 존재
- HS256 외 algorithm, 잘못된 signature/issuer/audience/type, claim 누락, 미래 iat 거부
- leeway 0에서 만료 즉시 `TokenExpired`, malformed token은 `InvalidToken`
- refresh 원문 SHA-256 hex와 `hmac.compare_digest` 검증

- [ ] **2.2 실패 확인**

실행: `uv run pytest --no-cov tests/auth/test_passwords.py tests/auth/test_tokens.py -q`

예상: auth primitive module이 없어 실패한다.

- [ ] **2.3 최소 구현**

`PasswordHash.recommended()`의 단일 instance로 Argon2 hash/verify 함수를 제공한다. 로그인용 dummy Argon2 hash를 모듈 상수로 두어 존재하지 않는 email도 verify 경로를 실행하게 한다.

`tokens.py`에는 `TokenType = Literal["access", "refresh"]`, typed claims dataclass, `create_token`, `decode_token`, `hash_refresh_token`, `refresh_token_matches`를 둔다. decode는 다음 옵션을 고정한다.

```python
jwt.decode(
    token,
    settings.secret_key,
    algorithms=["HS256"],
    audience=settings.audience,
    issuer=settings.issuer,
    leeway=settings.leeway_seconds,
    options={"require": ["sub", "jti", "type", "iat", "exp", "iss", "aud"]},
)
```

만료된 refresh session을 식별해야 할 때만 사용하는 `decode_expired_refresh_token`은 signature·issuer·audience·필수 claim·type을 그대로 검증하고 `verify_exp`만 false로 둔다. 일반 decode에는 이 우회를 사용하지 않는다.

- [ ] **2.4 통과 확인**

실행: `uv run pytest --no-cov tests/auth/test_passwords.py tests/auth/test_tokens.py -q`

예상: PASS.

- [ ] **2.5 커밋**

```bash
git add app/auth tests/auth/test_passwords.py tests/auth/test_tokens.py
git commit -m "feat: add Argon2 and JWT primitives"
```

### 작업 3: User와 RefreshSession 모델 및 Alembic revision 추가

**파일:**
- 생성: `app/models/user.py`
- 생성: `app/models/refresh_session.py`
- 수정: `app/models/__init__.py`
- 생성: `db/migrations/versions/20260715_0002_create_auth_resources.py`
- 수정: `tests/config/test_migrations.py`
- 수정: `tests/integration/test_migration.py`
- 생성: `tests/models/test_user.py`
- 생성: `tests/models/test_refresh_session.py`

- [ ] **3.1 실패 테스트 작성**

실제 PostgreSQL model/migration tests에 다음을 추가한다.

- users UUID PK, email 254자 unique, password_hash non-null, is_active true, timezone timestamps
- refresh_sessions UUID PK, user cascade FK, unique token_hash, expires/revoked timestamps, self FK `replaced_by_id`와 index
- user 삭제 시 refresh session cascade
- 새 head `20260715_0002`, table set에 users/refresh_sessions 포함
- 빈 DB base→head upgrade와 head→`20260714_0001` downgrade 후 auth table만 제거, 다시 head upgrade

- [ ] **3.2 실패 확인**

실행: `uv run pytest --no-cov tests/models/test_user.py tests/models/test_refresh_session.py tests/config/test_migrations.py tests/integration/test_migration.py -q`

예상: 모델/revision이 없고 기존 head 기대값 때문에 실패한다.

- [ ] **3.3 최소 구현**

`User`는 normalized email, Argon2 hash, active flag와 refresh_sessions 관계만 저장한다. `RefreshSession.id`가 JWT jti이며 `token_hash: String(64)`, `expires_at`, `revoked_at`, `replaced_by_id`, `created_at`을 선언한다. ORM 모델과 migration의 FK 이름·delete policy·index를 동일하게 맞춘다.

새 revision은 `down_revision = "20260714_0001"`이고 historical Example revision은 수정하지 않는다. downgrade는 self FK/index를 포함해 refresh_sessions를 먼저, users를 나중에 제거한다. `db/seeds.py`에는 인증 사용자를 추가하지 않는다.

- [ ] **3.4 통과 확인**

실행: `uv run pytest --no-cov tests/models/test_user.py tests/models/test_refresh_session.py tests/config/test_migrations.py tests/integration/test_migration.py -q`

예상: PASS.

- [ ] **3.5 Alembic 왕복 확인**

실행: `uv run alembic downgrade base`

실행: `uv run alembic upgrade head`

실행: `uv run alembic check`

예상: 독립 `TEST_DATABASE_URL`에서 성공하고 autogenerate diff가 없다.

- [ ] **3.6 커밋**

```bash
git add app/models/user.py app/models/refresh_session.py app/models/__init__.py db/migrations/versions/20260715_0002_create_auth_resources.py tests/config/test_migrations.py tests/integration/test_migration.py tests/models/test_user.py tests/models/test_refresh_session.py
git commit -m "feat: persist users and refresh sessions"
```

### 작업 4: Auth 입력 schema, 오류 catalog, 응답 serializer 추가

**파일:**
- 생성: `app/schemas/auth.py`
- 수정: `app/schemas/__init__.py`
- 생성: `app/serializers/user_serializer.py`
- 생성: `app/serializers/auth_token_serializer.py`
- 수정: `app/auth/tokens.py`
- 수정: `app/serializers/base.py`
- 수정: `app/serializers/__init__.py`
- 수정: `app/jsonapi/errors.py`
- 수정: `tests/jsonapi/test_errors.py`
- 생성: `tests/schemas/test_auth.py`
- 생성: `tests/serializers/test_auth_serializer.py`

- [ ] **4.1 실패 테스트 작성**

schema tests는 다음 resource document를 strict validation한다.

- register type `users`, email `EmailStr`/최대 254, password 12..128, extra/id/relationships 거부
- login type `authCredentials`, 동일 email/password 규칙
- refresh/logout type `refreshTokens`, non-empty refreshToken, extra 거부
- email 저장용 normalize 함수가 strip 후 casefold

serializer tests는 register User resource의 attributes가 정확히 `email`, `isActive`, `createdAt`, `updatedAt`, self가 `/api/v1/users/me`인지 확인한다. authTokens는 id가 refresh session UUID이고 attributes가 `accessToken`, `refreshToken`, `tokenType`, `expiresIn`, `refreshExpiresIn`이며 self link와 secret 이외 필드가 없어야 한다.

error parity test의 기대 code에 `AUTHENTICATION_REQUIRED`, `INVALID_CREDENTIALS`, `INVALID_TOKEN`, `TOKEN_EXPIRED`, `TOKEN_REVOKED`, `USER_INACTIVE`, `EMAIL_ALREADY_REGISTERED`를 추가하고 ko/en catalog가 정확히 일치하는지 확인한다.

- [ ] **4.2 실패 확인**

실행: `uv run pytest --no-cov tests/schemas/test_auth.py tests/serializers/test_auth_serializer.py tests/jsonapi/test_errors.py -q`

예상: 새 schema/serializer/error code가 없어 실패한다.

- [ ] **4.3 최소 구현**

세 concrete document model은 `ConfigDict(extra="forbid", strict=True, populate_by_name=True)`를 공유하되 resource type을 `Literal`로 고정한다. password는 공개 serializer에 선언하지 않는다.

`JsonApiSerializer`에 기본 구현이 기존과 같은 `resource_location(model)` classmethod를 추가하고 모든 self/relationship link 생성이 이 method를 사용하게 한다. `UserSerializer`만 이를 override해 `/api/v1/users/me`를 반환한다. `AuthTokenResource`는 `app/auth/tokens.py`의 frozen dataclass로 두고 `AuthTokenSerializer.resource_path = None`으로 self link를 만들지 않는다.

- [ ] **4.4 통과 확인**

실행: `uv run pytest --no-cov tests/schemas/test_auth.py tests/serializers/test_auth_serializer.py tests/serializers/test_example_serializer.py tests/jsonapi/test_errors.py -q`

예상: 신규 테스트 PASS, 기존 Example link 회귀 PASS.

- [ ] **4.5 커밋**

```bash
git add app/schemas/auth.py app/schemas/__init__.py app/serializers/user_serializer.py app/serializers/auth_token_serializer.py app/serializers/base.py app/serializers/__init__.py app/jsonapi/errors.py app/auth/tokens.py tests/schemas/test_auth.py tests/serializers/test_auth_serializer.py tests/serializers/test_example_serializer.py tests/jsonapi/test_errors.py
git commit -m "feat: define auth JSON API contracts"
```

### 작업 5: 회원가입과 로그인 endpoint 구현

**파일:**
- 생성: `app/controllers/api/v1/auth_controller.py`
- 생성: `app/auth/refresh_sessions.py`
- 수정: `app/controllers/api/v1/__init__.py`
- 수정: `config/routes.py`
- 생성: `tests/test_auth_controller.py`

- [ ] **5.1 실패 테스트 작성**

`create_app()`과 실제 PostgreSQL override를 사용하는 integration tests에 다음을 추가한다.

- register 201, normalized email, Argon2 저장, Location과 self `/api/v1/users/me`
- password/password_hash 응답·로그 미노출
- 잘못된 type/body는 안정적인 JSON:API 422 + pointer
- 같은 email의 대소문자/공백 중복 및 동시 등록은 409 `EMAIL_ALREADY_REGISTERED`
- login 200 token pair와 access/refresh claims/만료
- 없는 email과 틀린 password 모두 401 `INVALID_CREDENTIALS`로 같은 공개 detail
- inactive user는 403 `USER_INACTIVE`
- Accept 406, Content-Type 415, ko/en 오류와 vendor response type

- [ ] **5.2 실패 확인**

실행: `uv run pytest --no-cov tests/test_auth_controller.py -q`

예상: auth routes가 없어 404로 실패한다.

- [ ] **5.3 최소 구현**

`AuthController(prefix="/api/v1/auth")`는 `JsonApiRoute`, router-level `require_jsonapi_accept`, 각 body schema를 사용한다. register는 transaction 안에서 normalize→hash→flush→serialize하고 unique race를 `EMAIL_ALREADY_REGISTERED`로 변환한다.

login은 normalize한 email로 User를 조회하되 존재하지 않으면 dummy hash를 검증하고, 성공 시 transaction 안에서 access/refresh token과 RefreshSession을 만든다. `app/auth/refresh_sessions.py`의 `issue_token_pair(session, user, settings)`는 UUID jti를 먼저 정하고 refresh JWT hash를 가진 row와 공개 `AuthTokenResource`를 반환하되 호출자의 transaction을 commit하지 않는다. controller는 일반 dict/`application/json` 대신 `JsonApiResponse`만 반환한다.

`config/routes.py`에서 controller instance를 한 번 만들고 router를 명시적으로 include한다.

- [ ] **5.4 통과 확인**

실행: `uv run pytest --no-cov tests/test_auth_controller.py -q`

예상: PASS.

- [ ] **5.5 커밋**

```bash
git add app/auth/refresh_sessions.py app/controllers/api/v1/auth_controller.py app/controllers/api/v1/__init__.py config/routes.py tests/test_auth_controller.py
git commit -m "feat: add registration and login endpoints"
```

### 작업 6: Refresh 회전, 재사용 탐지, logout 구현

**파일:**
- 수정: `app/auth/refresh_sessions.py`
- 수정: `app/controllers/api/v1/auth_controller.py`
- 생성: `tests/auth/test_refresh_sessions.py`
- 수정: `tests/test_auth_controller.py`

- [ ] **6.1 실패 테스트 작성**

actual PostgreSQL tests로 다음 상태 전이를 단언한다.

- 정상 refresh: old row `revoked_at`, `replaced_by_id`; new row/token; 200 새 token pair
- old raw token 재사용: 401 `TOKEN_REVOKED`, 해당 user의 새 token을 포함한 모든 active session 폐기
- expired token: 해당 row만 폐기 후 401 `TOKEN_EXPIRED`
- unknown jti/hash mismatch/wrong type: 401 `INVALID_TOKEN`
- 두 thread가 barrier에서 같은 token을 동시에 refresh: 하나 200, 하나 `TOKEN_REVOKED`, 단일 회전 row와 reuse 정책에 따른 active session 0
- 다른 사용자의 active refresh session은 재사용 탐지에 영향받지 않음
- inactive user의 refresh는 해당 session을 폐기하고 403 `USER_INACTIVE`
- logout active token 204 빈 body, row 폐기; 같은 token 재로그아웃은 204; invalid token은 401
- logout expired token은 해당 row를 폐기한 뒤 401 `TOKEN_EXPIRED`

동시 refresh test는 setup user/session을 먼저 commit하고, 각 thread가 고유 `SessionFactory()`와 request client를 사용하게 한다. barrier 뒤 같은 raw token 요청을 실제로 경쟁시키고 thread마다 transaction을 독립적으로 종료한 다음 새 Session으로 최종 row/active-session 상태를 검증한다. 하나의 SQLAlchemy Session을 thread 사이에서 공유하지 않는다.

- [ ] **6.2 실패 확인**

실행: `uv run pytest --no-cov tests/auth/test_refresh_sessions.py tests/test_auth_controller.py -q`

예상: refresh/logout action과 session transition 함수가 없어 실패한다.

- [ ] **6.3 최소 구현**

`refresh_sessions.py`의 함수는 caller-owned Session과 이미 열린 transaction을 받아 다음 순서를 지키며 `commit`, `rollback`, 중첩 `begin()`을 호출하지 않는다.

1. token signature/claim/type 검증; 만료 시 signature를 유지한 expired decoder로 jti 식별
2. `select(RefreshSession).where(id == jti).with_for_update()`
3. stored hash와 `sub/user_id` 검증
4. expired면 해당 row만 폐기하고 `TOKEN_EXPIRED` outcome 반환
5. revoked면 같은 user의 `revoked_at IS NULL` rows를 모두 폐기하고 `TOKEN_REVOKED` outcome 반환
6. active면 old row 폐기, new pair/session 생성, `replaced_by_id` 연결 후 token pair outcome 반환

Auth controller는 `with session.begin(): outcome = rotate_refresh_session(...)`으로 transaction을 단독 소유한다. block이 정상 종료되어 폐기/회전 상태가 commit된 뒤 error outcome이면 `JsonApiException`을 raise하고, pair outcome이면 응답한다. 이를 통해 오류를 transaction 안에서 raise해 보안 상태 변경을 rollback하는 실수를 막는다. logout도 controller-owned transaction 안에서 helper outcome을 받고, signature/claims/hash가 유효한 row는 active면 폐기하며 이미 폐기된 같은 row는 204로 멱등 처리한다.

- [ ] **6.4 통과 확인**

실행: `uv run pytest --no-cov tests/auth/test_refresh_sessions.py tests/test_auth_controller.py -q`

예상: PASS.

- [ ] **6.5 커밋**

```bash
git add app/auth/refresh_sessions.py app/controllers/api/v1/auth_controller.py tests/auth/test_refresh_sessions.py tests/test_auth_controller.py
git commit -m "feat: rotate and revoke refresh sessions"
```

### 작업 7: Bearer dependency, 현재 사용자, Example 쓰기 보호 구현

**파일:**
- 생성: `app/auth/dependencies.py`
- 생성: `app/controllers/api/v1/users_controller.py`
- 수정: `app/controllers/api/v1/__init__.py`
- 수정: `app/controllers/concerns/crud_actions.py`
- 수정: `app/controllers/api/v1/examples_controller.py`
- 수정: `config/database.py`
- 수정: `config/routes.py`
- 생성: `tests/auth/test_dependencies.py`
- 생성: `tests/test_user_controller.py`
- 수정: `tests/controllers/test_crud_actions.py`
- 수정: `tests/controllers/test_relationship_actions.py`
- 수정: `tests/config/test_database.py`
- 수정: `tests/test_example_controller.py`

- [ ] **7.1 실패 테스트 작성**

dependency tests는 Authorization 누락, malformed Bearer, refresh-as-access, invalid/expired claims, 삭제된 user가 각각 401의 올바른 code인지 확인한다. inactive user는 `get_current_user`로 조회되지만 `get_current_active_user`에서 403 `USER_INACTIVE`여야 한다.

factory integration tests는 다음을 검증한다.

- `GET /api/v1/users/me` valid access token 200 + UserSerializer, 누락/invalid 401
- Example의 index/show/relationship/related GET은 token 없이 기존대로 성공
- POST/PATCH/PUT/DELETE와 category/tags mutation은 token 없이 `AUTHENTICATION_REQUIRED`
- valid active access token으로 기존 write 계약 성공, inactive token은 403
- OpenAPI에 HTTP Bearer scheme과 보호 operation security가 있고 공개 GET에는 security가 없음
- auth lookup Session과 CrudActions Session이 서로 다른 request-scoped instance이며, auth SELECT가 transaction을 연 상태에서도 보호 POST가 `InvalidRequestError` 없이 commit

- [ ] **7.2 실패 확인**

실행: `uv run pytest --no-cov tests/auth/test_dependencies.py tests/test_user_controller.py tests/test_example_controller.py tests/controllers/test_crud_actions.py tests/controllers/test_relationship_actions.py tests/config/test_database.py -q`

예상: dependency/me route가 없고 CrudActions가 read/write dependencies를 구분하지 않아 실패한다.

- [ ] **7.3 최소 구현**

`HTTPBearer(auto_error=False, scheme_name="BearerAuth")`를 사용해 `get_current_user`와 `get_current_active_user`를 구현한다. access claim의 sub로 request-scoped auth Session에서 User를 조회하며 secret/원문 token을 request state나 log에 저장하지 않는다.

`config/database.py`에는 같은 `SessionFactory`를 사용하지만 별도 instance를 yield/close하는 `get_auth_session()` dependency를 추가한다. auth dependency는 이를 사용하고 CrudActions는 기존 `get_session()`을 사용한다. SQLAlchemy 2의 auth SELECT가 autobegin한 transaction이 CRUD의 `with session.begin()`과 충돌하지 않도록 두 dependency를 합치거나 `begin_nested()`으로 바꾸지 않는다. factory tests는 두 dependency를 모두 test engine으로 override하고 `tests/config/test_database.py`는 각각 close를 검증한다.

`CrudActions`에 아래 선언을 추가한다.

```python
read_dependencies: tuple[Callable[..., Any], ...] = ()
write_dependencies: tuple[Callable[..., Any], ...] = ()
```

GET resource/relationship routes에는 read dependencies, POST/PATCH/PUT/DELETE 및 relationship mutation에는 write dependencies를 `Depends`로 등록한다. router-level JSON:API Accept dependency는 유지한다. `ExamplesController.write_dependencies = (get_current_active_user,)`만 선언해 controller를 얇게 유지한다.

보호 route의 OpenAPI response에는 401 `AUTHENTICATION_REQUIRED`와 403 `USER_INACTIVE`가 보이도록 `_ERROR_DESCRIPTIONS`와 route response 선언을 함께 갱신한다. 공개 GET의 response/security 계약은 바꾸지 않는다.

`UsersController`는 `/api/v1/users/me` GET 하나만 등록하고 `UserSerializer.document(current_user)`를 반환한다. `config/routes.py`에 명시적으로 한 번 include한다.

- [ ] **7.4 통과 확인**

실행: `uv run pytest --no-cov tests/auth/test_dependencies.py tests/test_user_controller.py tests/test_example_controller.py tests/controllers/test_crud_actions.py tests/controllers/test_relationship_actions.py tests/config/test_database.py -q`

예상: PASS.

- [ ] **7.5 커밋**

```bash
git add app/auth/dependencies.py app/controllers/api/v1/users_controller.py app/controllers/api/v1/examples_controller.py app/controllers/api/v1/__init__.py app/controllers/concerns/crud_actions.py config/database.py config/routes.py tests/auth/test_dependencies.py tests/test_user_controller.py tests/test_example_controller.py tests/controllers/test_crud_actions.py tests/controllers/test_relationship_actions.py tests/config/test_database.py
git commit -m "feat: protect Example writes with bearer auth"
```

### 작업 8: RedisBroker와 Example Dramatiq actor 추가

**파일:**
- 생성: `config/broker.py`
- 생성: `app/jobs/__init__.py`
- 생성: `app/jobs/example.py`
- 생성: `tests/config/test_broker.py`
- 생성: `tests/jobs/test_process_example.py`

- [ ] **8.1 실패 테스트 작성**

broker tests는 `REDIS_URL` 기본/override, `RedisBroker(url=...)`, `dramatiq.set_broker()` 호출, jobs package가 actor import 전에 configure하는 순서를 확인한다. API의 `create_app()`은 RedisBroker를 만들지 않아야 한다.

actor tests는 patched StubBroker와 실제 PostgreSQL을 사용해 다음을 확인한다.

- `process_example.send(str(id))`가 문자열 payload message를 enqueue하고 worker가 소비
- actor options `max_retries=3`, `min_backoff=15000`
- 유효 Example은 공개 필드 변경 없이 구조화 log 후 성공
- malformed UUID와 missing Example은 warning 후 성공하여 retry message 없음
- SessionFactory DB `OperationalError`는 밖으로 전파되어 Dramatiq retry middleware 대상이 됨
- `StubBroker(middleware=[Retries(min_backoff=0, max_backoff=0)])`와 실제 `Worker`에서 첫 DB 호출 `OperationalError`, 두 번째 호출 성공으로 같은 message가 재enqueue·재소비됨
- 같은 message를 두 번 실행해도 DB state가 동일

- [ ] **8.2 실패 확인**

실행: `uv run pytest --no-cov tests/config/test_broker.py tests/jobs/test_process_example.py -q`

예상: broker/jobs package가 없어 실패한다.

- [ ] **8.3 최소 구현**

`config/broker.py`의 `configure_broker()`는 `REDIS_URL` 기본 `redis://localhost:6379/0`으로 `RedisBroker`를 만들고 `dramatiq.set_broker(broker)` 후 broker를 반환한다. API factory와 `config/routes.py`에서는 이 module을 import하지 않는다.

`app/jobs/__init__.py`는 다음 순서를 유지한다.

```python
from config.broker import configure_broker

configure_broker()
from app.jobs.example import process_example  # noqa: E402
```

actor는 `@dramatiq.actor(max_retries=3, min_backoff=15_000)`로 선언한다. 각 실행에서 `with SessionFactory() as session`을 열고 UUID parse/missing resource만 warning+return하며 SQLAlchemy 예외는 catch하지 않는다. Example을 update/commit하거나 CRUD에서 자동 enqueue하지 않는다.

retry integration test는 production actor options `3/15000`을 먼저 단언한 뒤, 지연 없는 결정적 테스트를 위해 `process_example.options`의 `max_retries=1`, `min_backoff=0`, `max_backoff=0`을 테스트 범위에서만 임시 적용한다. decoration 시 Actor가 broker를 보유하므로 원래 actor options, `process_example.broker`, global broker를 모두 먼저 저장한다. `StubBroker(middleware=[Retries(min_backoff=0, max_backoff=0)])`를 만든 뒤 global broker뿐 아니라 `process_example.broker`도 stub으로 바꾸고 `stub.declare_actor(process_example)`를 호출한 다음 `emit_after("process_boot")`와 `Worker(..., worker_timeout=100)`을 구성한다. 반드시 `worker.start()` 후 message를 enqueue하고 `broker.join(process_example.queue_name)`과 `worker.join()` 순서로 소비 완료를 기다린다. SessionFactory mock의 첫 호출은 `OperationalError`, 두 번째 호출은 Example 조회 성공을 반환하며 호출 횟수 2와 dead letter 부재를 단언한다. 전체 실행을 `try/finally`로 감싸 `worker.stop()`을 항상 호출하고 actor options, `process_example.broker`, global broker를 원복하며 원래 broker에 actor를 다시 선언해 thread와 전역 상태가 다음 테스트에 누출되지 않게 한다.

- [ ] **8.4 통과 확인**

실행: `uv run pytest --no-cov tests/config/test_broker.py tests/jobs/test_process_example.py -q`

예상: PASS, 실제 Redis 연결 없음.

- [ ] **8.5 커밋**

```bash
git add config/broker.py app/jobs/__init__.py app/jobs/example.py tests/config/test_broker.py tests/jobs/test_process_example.py
git commit -m "feat: add Dramatiq Redis worker example"
```

### 작업 9: Health endpoint와 Compose worker topology 구현

**파일:**
- 생성: `app/controllers/health_controller.py`
- 수정: `config/routes.py`
- 수정: `Dockerfile`
- 수정: `docker-compose.yml`
- 수정: `.env.example`
- 생성: `tests/test_health_controller.py`
- 생성: `tests/config/test_compose.py`

- [ ] **9.1 실패 테스트 작성**

health tests는 `/health/live`가 DB 접근 없이 200, `/health/ready`가 `SELECT 1` 성공 시 200·DB exception 시 503인지 확인한다. 두 route는 Accept header가 없어도 응답하되 성공은 `data: null`과 `meta.status=ok`, 실패는 `errors`를 가진 `application/vnd.api+json` 문서를 사용한다.

Compose test는 `docker compose config --format json`을 파싱해 다음을 단언한다.

- db/migrate/api 유지, redis/worker 추가, postgres_data/redis_data volume
- Redis healthcheck
- worker command 정확히 `dramatiq app.jobs`
- worker는 migrate complete와 redis healthy 의존
- API는 migrate만 의존하고 Redis healthy를 readiness/depends_on으로 요구하지 않음
- API에 JWT settings와 REDIS_URL, worker에 DATABASE_URL/REDIS_URL 전달
- 개발 secret은 32바이트 이상이며 `.env.example`에서 development-only 표시
- Docker healthcheck `/health/ready`

- [ ] **9.2 실패 확인**

실행: `uv run pytest --no-cov tests/test_health_controller.py tests/config/test_compose.py -q`

예상: health route, redis, worker가 없어 실패한다.

- [ ] **9.3 최소 구현**

`HealthController`는 prefix 없이 `/health/live`, `/health/ready`를 등록한다. 성공은 required data field를 명시한 `JsonApiResponse(SuccessDocument(data=None, meta={"status": "ok"}))`로 반환한다. ready는 request Session에서 `select(1)`을 실행하며 SQLAlchemy error를 외부 detail 없이 503 `INTERNAL_SERVER_ERROR` JSON:API document로 바꾼다. health route는 Accept dependency를 강제하지 않지만 일반 `application/json` 응답을 만들지 않는다.

Compose에 Redis service/volume과 worker를 추가한다. API는 JWT env를 받아 factory validation을 통과하지만 Redis 장애에도 시작한다. migrate는 auth revision까지 `alembic upgrade head`, worker는 migration/Redis 뒤 `dramatiq app.jobs`를 실행한다.

`.env.example`에는 `JWT_SECRET_KEY`, `JWT_ISSUER`, `JWT_AUDIENCE`, `JWT_ACCESS_EXPIRES_SECONDS=900`, `JWT_REFRESH_EXPIRES_SECONDS=2592000`, `JWT_LEEWAY_SECONDS=0`, `REDIS_URL`을 추가한다. secret은 예제 개발값이며 production에서 교체해야 한다는 주석을 둔다.

- [ ] **9.4 통과 확인**

실행: `uv run pytest --no-cov tests/test_health_controller.py tests/config/test_compose.py -q`

실행: `docker compose config --quiet`

예상: 모두 PASS.

- [ ] **9.5 커밋**

```bash
git add app/controllers/health_controller.py config/routes.py Dockerfile docker-compose.yml .env.example tests/test_health_controller.py tests/config/test_compose.py
git commit -m "feat: add health checks and worker compose stack"
```

### 작업 10: CI와 한국어 운영 문서 추가

**파일:**
- 생성: `.github/workflows/ci.yml`
- 수정: `README.md`
- 수정: `tests/test_example_controller.py`
- 생성: `tests/docs/test_readme.py`

- [ ] **10.1 실패 테스트 작성**

README contract test는 다음 필수 내용을 확인한다.

- development secret과 production 32바이트 요구사항
- register/login/refresh/logout/me JSON:API curl
- Bearer token Example write curl
- refresh token 안전 보관, logout 뒤 access token 최대 15분 유효
- `dramatiq app.jobs`와 `process_example.send(str(example.id))`
- Compose Redis/worker 확인 및 전체 검증 명령

factory route/OpenAPI 기대값은 auth/users/health 경로와 Bearer security scheme을 포함하도록 갱신한다. CI workflow는 checkout, uv setup, `uv sync --frozen`, `./scripts/check.sh`, `docker compose config --quiet`, production Docker build를 실행해야 한다.

- [ ] **10.2 실패 확인**

실행: `uv run pytest --no-cov tests/docs/test_readme.py tests/test_example_controller.py -q`

예상: README/route 기대값에 인증·worker가 없어 실패한다.

- [ ] **10.3 최소 구현**

README를 기존 Example 설명에 인증과 비동기 작업 섹션을 추가하는 방식으로 한국어 갱신한다. token을 cookie에 저장한다는 예시를 넣지 않고 JSON body 발급과 Authorization Bearer 사용을 구분한다. actor 자동 enqueue가 아님을 명시한다.

CI는 `./scripts/check.sh`가 생성하는 임시 PostgreSQL을 그대로 사용하고 별도 SQLite shortcut을 두지 않는다. Docker build는 runtime image가 실제 앱/auth/jobs를 모두 포함하는지 검증한다.

- [ ] **10.4 통과 확인**

실행: `uv run pytest --no-cov tests/docs/test_readme.py tests/test_example_controller.py -q`

예상: PASS.

- [ ] **10.5 커밋**

```bash
git add .github/workflows/ci.yml README.md tests/docs/test_readme.py tests/test_example_controller.py
git commit -m "docs: document JWT and Dramatiq workflows"
```

### 작업 11: 전체 보안·품질·컨테이너 검증

**파일:**
- 아래 명령에서 발견된 실패를 고치는 데 필요한 파일만 수정하며 범위를 확장하지 않는다.

- [ ] **11.1 전체 정적·PostgreSQL 테스트 gate**

실행: `uv sync --frozen`

예상: lockfile과 pyproject 일치.

실행: `./scripts/check.sh`

예상: Ruff lint/format, strict mypy, PostgreSQL pytest, coverage 80% 이상, detect-secrets 모두 PASS.

- [ ] **11.2 Migration/Compose/build gate**

실행: `docker compose config --quiet`

예상: exit 0.

실행: `docker build -t template-python-fastapi:verification .`

예상: runtime image build 성공.

실행: `docker compose -p template-python-fastapi-verification down -v --remove-orphans`

예상: 이전 검증 project와 volume이 없어짐; project가 없었던 경우도 exit 0.

실행: `docker compose -p template-python-fastapi-verification up -d --build --wait`

예상: db/redis/api healthy, migration 성공, worker running.

- [ ] **11.3 실제 health와 인증 흐름 smoke test**

실행: `curl -fsS http://localhost:4000/health/live`

예상: HTTP 200.

실행: `curl -fsS http://localhost:4000/health/ready`

예상: HTTP 200.

실행: `curl -i -sS -X POST -H 'Accept: application/vnd.api+json' -H 'Content-Type: application/vnd.api+json' --data '{"data":{"type":"users","attributes":{"email":"compose@example.com","password":"compose-password-123"}}}' http://localhost:4000/api/v1/auth/register` <!-- pragma: allowlist secret -->

예상: HTTP 201, Location `/api/v1/users/me`, password 미노출.

실행: `ACCESS_TOKEN="$(curl -fsS -X POST -H 'Accept: application/vnd.api+json' -H 'Content-Type: application/vnd.api+json' --data '{"data":{"type":"authCredentials","attributes":{"email":"compose@example.com","password":"compose-password-123"}}}' http://localhost:4000/api/v1/auth/login | python -c 'import json,sys; print(json.load(sys.stdin)["data"]["attributes"]["accessToken"])')"` <!-- pragma: allowlist secret -->

예상: HTTP 200 authTokens document에서 accessToken을 현재 shell 변수에만 저장하고 stdout·파일·커밋에는 기록하지 않는다.

실행: `curl -i -sS -X POST -H 'Accept: application/vnd.api+json' -H 'Content-Type: application/vnd.api+json' -H "Authorization: Bearer ${ACCESS_TOKEN}" --data '{"data":{"type":"examples","attributes":{"title":"Protected Example","status":"active","score":80}}}' http://localhost:4000/api/v1/examples`

예상: 실제 token으로 HTTP 201.

실행: `unset ACCESS_TOKEN`

예상: shell에서 access token 제거.

- [ ] **11.4 Worker smoke test와 정리**

실행: `docker compose -p template-python-fastapi-verification exec api python -c 'from app.jobs import process_example; process_example.send("00000000-0000-4000-8000-000000000000")'`

예상: message enqueue 성공, worker log에 missing Example warning, retry loop 없음.

실행: `docker compose -p template-python-fastapi-verification logs --no-color worker`

예상: RabbitMQ connection 시도 없음, Dramatiq worker가 Redis broker로 message 처리.

실행: `docker compose -p template-python-fastapi-verification down -v --remove-orphans`

예상: exit 0, 검증 자원 정리.

실행: `docker compose -f docker-compose.test.yml -p template-python-fastapi-plan down -v`

예상: exit 0, 계획 실행용 `_test` PostgreSQL 정리.

- [ ] **11.5 잔존 보안/범위 검토**

실행: `rg -n 'JWT_SECRET_KEY.*(changeme|default|fallback)' app config`

예상: no matches; production fallback secret 없음.

실행: `rg -n 'logger\..*(token|password)|print\(.*(token|password)' app config`

예상: no matches; token·password log/print 없음.

실행: `git diff --check`

예상: no whitespace errors.

- [ ] **11.6 검증 수정 처리**

검증 실패가 있으면 실패를 소유한 작업으로 돌아가 그 작업의 파일 범위 안에서 수정·재검증·커밋한 뒤 작업 11 전체를 처음부터 다시 실행한다. 실패가 없으면 새 커밋을 만들지 않는다.

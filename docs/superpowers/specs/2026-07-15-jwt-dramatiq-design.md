# FastAPI JWT 인증 및 Dramatiq 작업 큐 설계

## 목표

`template-python-fastapi`에 자체 JWT 인증과 Redis 기반 Dramatiq 작업 큐를 추가한다. 기존의 동기식 SQLAlchemy, PostgreSQL, 엄격한 JSON:API 계약을 유지하면서 인증이 필요한 쓰기 작업과 별도 worker 실행 패턴을 템플릿 수준으로 제공한다.

## 성공 기준

- Argon2 비밀번호 해시와 자체 사용자 저장소를 제공한다.
- access token과 회전 가능한 refresh token을 제공한다.
- 회원가입, 로그인, 갱신, 로그아웃, 현재 사용자 API가 JSON:API 계약을 따른다.
- Example 읽기는 공개하고 쓰기는 활성 사용자 JWT를 요구한다.
- Redis 기반 Dramatiq worker와 Example actor 예제를 제공한다.
- Docker Compose로 PostgreSQL, Redis, migration, API, worker를 함께 실행할 수 있다.
- 인증·토큰 회전·작업 큐를 실제 PostgreSQL 및 StubBroker 테스트로 검증한다.
- 기존 80% coverage gate, strict mypy, Ruff, detect-secrets를 유지한다.
- `/health/live`와 PostgreSQL 기반 `/health/ready`를 제공하고 Docker healthcheck에 사용한다.

## 비목표

- OAuth/OIDC 공급자, 소셜 로그인, MFA, 이메일 인증을 추가하지 않는다.
- 역할·권한·조직 모델을 추가하지 않는다.
- 비동기 SQLAlchemy session으로 전환하지 않는다.
- Dramatiq scheduler 또는 주기 작업 관리 UI를 추가하지 않는다.
- Example JSON:API 공개 필드나 관계를 변경하지 않는다.

## 사용자와 인증 세션 모델

### User

- UUID 기본 키
- 앞뒤 공백 제거와 `casefold()`를 적용한 email, 최대 254자, 고유 인덱스
- Argon2 `password_hash`
- `is_active`, 기본값 true
- timezone-aware timestamps

email은 Pydantic `EmailStr`로 구문을 검증한 뒤 저장 직전에 정규화한다. 비밀번호는 12~128자이며 원문과 hash는 serializer, log, 오류에 포함하지 않는다.

### RefreshSession

- UUID 기본 키이자 refresh token의 `jti`
- `user_id` 외래 키, 사용자 삭제 시 cascade
- 원본 refresh token의 SHA-256 hash
- `expires_at`, `revoked_at`, `replaced_by_id`
- 생성 시각

갱신 시 현재 row를 `FOR UPDATE`로 잠그고 기존 세션을 폐기한 뒤 새 세션과 token을 같은 transaction에서 만든다. 이미 폐기된 token이 다시 제시되면 `replaced_by_id` 체인을 포함한 해당 사용자의 활성 refresh session을 모두 폐기해 탈취 token 재사용을 차단한다. 만료된 token은 해당 session만 폐기한다.

## JWT와 비밀번호 정책

- 비밀번호 해시는 `pwdlib[argon2]`를 사용한다.
- JWT는 PyJWT의 HS256을 사용한다.
- access token 기본 만료는 15분이다.
- refresh token 기본 만료는 30일이다.
- token은 `sub`, `jti`, `type`, `iat`, `exp`, `iss`, `aud` claim을 포함한다.
- access와 refresh token은 `type`이 다르며 용도를 바꿔 사용할 수 없다.
- `JWT_SECRET_KEY`는 운영에서 필수이며 최소 32바이트를 요구한다.
- decode는 알고리즘을 `HS256` 하나로 고정하고 `exp`, `iat`, `sub`, `jti`, `type`, `iss`, `aud` 존재와 issuer·audience 일치를 모두 검증한다. clock leeway 기본값은 0초다.
- Compose의 기본 secret은 개발 전용임을 README와 `.env.example`에 명시한다.
- 애플리케이션 factory는 인증 router를 등록하기 전에 설정을 검증한다. Compose 밖에서는 secret 누락 또는 32바이트 미만이면 시작을 실패시키며 암묵적 fallback secret을 사용하지 않는다.

## 인증 API

| 메서드 | 경로 | 요청 type | 결과 |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/register` | `users` | 사용자 생성 `201` |
| `POST` | `/api/v1/auth/login` | `authCredentials` | access·refresh token `200` |
| `POST` | `/api/v1/auth/refresh` | `refreshTokens` | 회전된 token 쌍 `200` |
| `POST` | `/api/v1/auth/logout` | `refreshTokens` | refresh session 폐기 `204` |
| `GET` | `/api/v1/users/me` | 없음 | 현재 사용자 `200` |

register는 `users`의 `email`, `password` attributes를 받고 `users` resource를 반환한다. 공개 attributes는 `email`, `isActive`, `createdAt`, `updatedAt`이며 `Location`과 self link는 `/api/v1/users/me`다.

login은 `authCredentials`의 `email`, `password`를 받고 `authTokens` resource를 반환한다. resource id는 refresh session UUID이고 attributes는 `accessToken`, `refreshToken`, `tokenType`(`Bearer`), `expiresIn`(900), `refreshExpiresIn`(2592000)이다. refresh와 logout은 `refreshTokens`의 `refreshToken` attribute를 받는다. token은 JSON body로만 전달하며 cookie에는 저장하지 않는다.

요청과 응답은 `application/vnd.api+json`을 사용한다. 인증 오류 code는 `AUTHENTICATION_REQUIRED`, `INVALID_CREDENTIALS`, `INVALID_TOKEN`, `TOKEN_EXPIRED`, `TOKEN_REVOKED`, `USER_INACTIVE`, `EMAIL_ALREADY_REGISTERED`로 고정하고 기존 한·영 오류 catalog에 추가한다. 로그인 실패는 email 존재 여부를 구분하지 않는다. logout은 refresh session만 폐기하며 이미 발급된 access token은 최대 15분 동안 유효하다는 점을 문서화한다.

## 인증 의존성과 Example 경계

`get_current_user`와 `get_current_active_user`를 재사용 가능한 FastAPI dependency로 제공한다. Bearer access token의 서명·issuer·audience·만료·type을 검사한 뒤 요청 단위 DB session으로 사용자를 조회한다.

`CrudActions`는 읽기와 쓰기에 서로 다른 dependency 목록을 선언할 수 있도록 확장한다. `ExamplesController`는 GET 계열을 공개하고 POST·PATCH·PUT·DELETE 및 관계 변경을 `get_current_active_user`로 보호한다. 인증 dependency는 비즈니스 필드나 serializer 표현에 영향을 주지 않는다.

## Dramatiq 작업 큐

### Broker

- `dramatiq[redis]`와 RedisBroker를 사용한다.
- `REDIS_URL` 한 개로 API와 worker가 같은 broker를 사용한다.
- actor enqueue 시에만 broker 연결이 필요하며 API factory import가 Redis 상태에 묶이지 않게 한다.
- worker process는 `dramatiq app.jobs` 명령으로 명시적으로 시작한다.
- `config/broker.py`의 `configure_broker()`가 `RedisBroker(url=REDIS_URL)`를 만들고 `dramatiq.set_broker()`를 호출한다. `app/jobs/__init__.py`는 actor module을 import하기 전에 이 함수를 실행해 기본 RabbitMQ broker가 선택되지 않게 한다.

### Example actor

`process_example(example_id: str)` actor를 제공한다.

- UUID 형식을 검증한다.
- 작업마다 동기식 `SessionFactory` session을 연다.
- UUID가 잘못됐거나 Example이 없으면 warning log를 남기고 정상 반환해 재시도하지 않는다.
- DB 예외는 actor 밖으로 전파해 Dramatiq retry middleware가 처리한다.
- actor는 `max_retries=3`, `min_backoff=15000`으로 선언하고 지수 backoff를 사용한다.
- 처리 결과는 구조화된 log로 남기고 Example 공개 필드를 변경하지 않는다.
- 같은 message가 중복 실행돼도 외부 상태가 달라지지 않는 멱등성을 유지한다.

actor 자동 enqueue는 CRUD 기본 동작에 넣지 않는다. README에서 도메인 hook이나 명시적 서비스 코드에서 `process_example.send(str(example.id))`를 호출하는 방법을 제시해 템플릿 사용자가 side effect 시점을 선택하게 한다.

## Docker Compose

기존 `db`, `migrate`, `api`에 `redis`, `worker`를 추가한다.

- `redis`: healthcheck와 영속 volume
- `worker`: migration 완료와 Redis health 이후 Dramatiq 시작
- `api`: JWT와 Redis 환경 변수를 받지만 Redis가 없어도 읽기 API와 인증 검증은 시작 가능
- `migrate`: 인증 테이블 migration까지 적용

test Compose의 PostgreSQL 격리 동작은 유지한다. Dramatiq 단위 테스트는 StubBroker를 사용해 로컬 검증에 Redis를 강제하지 않는다.

`/health/live`는 process 생존만 확인하고 `/health/ready`는 PostgreSQL `SELECT 1`을 실행한다. Redis 장애는 읽기·인증 API의 readiness를 막지 않으며 worker 상태는 Compose process 상태로 분리한다. Dockerfile healthcheck는 `/health/ready`를 사용한다.

## 마이그레이션과 시드

새 Alembic revision에서 `users`와 `refresh_sessions`를 만들고 downgrade에서 역순 제거한다. Example migration은 수정하지 않는다. 기본 seed에는 비밀번호나 기본 관리자를 넣지 않는다. 인증 사용자는 register API 또는 명시적 애플리케이션 코드로 만든다.

## 테스트 전략

- 보안 단위 테스트: Argon2 hash/verify, JWT claim·type·issuer·audience·만료
- 인증 통합 테스트: register, 중복 email, login 성공·실패, 비활성 사용자
- refresh 테스트: 정상 회전, 이전 token 재사용 시 전체 활성 session 폐기, 만료·폐기, 동시 갱신
- dependency 테스트: Bearer 누락·오류·비활성 사용자와 현재 사용자
- Example 요청 테스트: 공개 GET, 인증 필수 쓰기, 유효 JWT 쓰기
- migration 테스트: upgrade/downgrade 및 제약·인덱스
- actor 테스트: enqueue payload, 정상 처리, 없는 Example, DB 일시 오류 retry
- broker 테스트: StubBroker를 통한 메시지 소비와 실패 재시도
- Compose 테스트: Redis와 worker 구성, dependency health 조건
- 설정 테스트: secret 필수·최소 길이, issuer·audience·필수 claim, 고정 알고리즘
- health 테스트: live 성공, DB 정상 ready, DB 장애 ready 실패

전체 `./scripts/check.sh`는 Ruff, format, strict mypy, PostgreSQL pytest, 80% coverage, detect-secrets를 계속 통과해야 한다. FastAPI 저장소에도 같은 검증 명령과 Docker build를 실행하는 GitHub Actions CI를 추가한다.

## 문서와 설정

README에 다음 내용을 한국어로 추가한다.

- 개발용 JWT secret과 운영 secret 요구 조건
- register/login/refresh/logout/me JSON:API 예제
- Bearer token으로 Example 쓰기 요청을 보내는 예제
- worker 실행과 actor enqueue 예제
- Compose에서 Redis와 worker를 확인하는 방법
- refresh token을 클라이언트에서 안전하게 보관해야 한다는 주의
- logout 이후 기존 access token의 최대 15분 유효 기간

`.env.example`에는 `JWT_ISSUER=template-python-fastapi`, `JWT_AUDIENCE=template-python-fastapi`, access·refresh 만료, `JWT_SECRET_KEY`, `REDIS_URL`을 추가한다. OpenAPI에는 HTTP Bearer security scheme이 나타나야 하며 실제 JSON:API 오류 응답과 일치해야 한다.

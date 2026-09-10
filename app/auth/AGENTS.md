<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# 인증 기본 기능 지침

## Purpose

비밀번호 검증, HS256 JWT 생성·검증, bearer 사용자 조회와 PostgreSQL refresh session 수명을 담당한다. HTTP 문서·오류 응답은 controller와 JSON:API 계층에 맡기고, 이곳의 DB 함수는 호출자가 시작한 transaction에 변경을 적재한다.

## Key Files

| 파일 | 역할 |
| --- | --- |
| `__init__.py` | 인증 패키지 표시 |
| `passwords.py` | pwdlib 권장 Argon2 hash·검증과 미등록 계정용 dummy hash |
| `tokens.py` | access/refresh JWT, 엄격한 claim 검사, typed 결과, SHA-256 digest·상수 시간 비교 |
| `dependencies.py` | Authorization 검증, 짧은 조회 session을 닫은 뒤 detached 현재 사용자·활성 사용자 반환 |
| `refresh_sessions.py` | 사용자 잠금, 토큰 쌍 발급, 회전·replay 폐기·logout |

## For AI Agents

### Working In This Directory

- JWT는 HS256만 허용하고 `sub`, UUID `jti`, `type`, `iat`, `exp`, `iss`, `aud`를 검사한다. access/refresh 타입, 설정 issuer·audience, 숫자 날짜와 시간대 의미를 유지한다.
- `decode_expired_refresh_token()`은 만료만 우회한다. 서명·필수 claim·타입 검사를 그대로 유지하며, 만료된 세션의 폐기를 기록하는 흐름에서만 사용한다.
- 비밀번호는 Argon2 hash, refresh token은 SHA-256 hex digest만 저장한다. raw refresh token 비교는 `hmac.compare_digest`를 사용한다. 기존 dummy hash 검증 경로를 유지해 로그인에서 이메일 존재 여부를 구분하지 않는다.
- `get_current_user`는 access bearer와 DB 사용자 존재를 확인한다. `get_auth_session_factory`의 session을 query 동안만 열고 User를 expunge·close한 뒤 detached 상태로 반환한다. endpoint session이 연결을 얻기 전에 인증 연결을 풀에 돌려준다. `get_current_active_user`가 비활성 계정을 403으로 거부한다. 두 의존성을 같은 것으로 취급하지 않는다.
- 오류는 `AUTHENTICATION_REQUIRED`, `INVALID_TOKEN`, `TOKEN_EXPIRED`, `USER_INACTIVE` 등 안정된 코드로 전달하며 Authorization 오류의 source header를 보존한다.

### Refresh Session과 Transaction

- 발급·회전·logout은 사용자 행을 `FOR UPDATE`로 먼저 잠근다. 회전·logout은 이어서 refresh session 행을 잠근다. `populate_existing=True`로 사용자 상태를 다시 읽고 이 잠금 순서를 모든 mutation에서 유지한다.
- `issue_token_pair_for_locked_user()`는 사용자 잠금을 이미 가진 호출자만 사용한다. 자체 commit은 없으며, 새 refresh JWT의 `jti`가 `RefreshSession.id`와 응답 토큰 자원 ID가 된다.
- 회전은 `sub`·저장 user_id·digest를 확인한 뒤 기존 세션을 폐기하고 새 토큰 쌍을 추가·flush하여 `replaced_by_id`를 연결한다.
- 만료는 replay 검사보다 먼저 판정하며 해당 세션을 폐기하고 401 `TOKEN_EXPIRED`를 반환한다. 아직 만료되지 않은 폐기 토큰의 재사용은 같은 사용자의 모든 미폐기 세션을 폐기하고 401 `TOKEN_REVOKED`를 반환한다.
- `_load_verified_refresh_session()`이 decode·사용자/세션 잠금·digest·만료 검사를 회전과 logout에 공통 제공한다. 검사 순서를 한쪽에만 복사하지 않는다.
- 비활성 사용자의 refresh는 해당 세션을 폐기하고 403 `USER_INACTIVE`를 반환한다. 정상 logout은 해당 세션만 폐기하며, 유효기간 내 반복 logout은 성공한다.
- 보안 상태 변경 뒤의 예상 오류는 예외를 즉시 던지지 않고 `RefreshSessionError` 값으로 반환한다. AuthController가 transaction을 종료한 뒤 HTTP 예외로 변환하므로 폐기 기록이 rollback되지 않는다.
- 동일 사용자에 대한 동시 발급·회전·replay를 사용자 잠금으로 직렬화하되 다른 사용자를 전역 잠금으로 묶지 않는다. raw token이나 password를 로그·오류에 노출하지 않는다.

### Testing Requirements

`TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리킬 때 `uv run pytest --no-cov tests/auth tests/test_auth_controller.py tests/test_user_controller.py -q`를 실행한다. URL이 없으면 `./scripts/check.sh`의 임시 Docker PostgreSQL 게이트를 사용한다. refresh 경쟁은 실제 PostgreSQL에서 같은 사용자 회전·발급과 replay의 순서, 다른 사용자 잠금 독립성, 폐기 후 오류 응답의 commit을 검증한다. 최종 API 게이트는 루트 지침을 따른다.

### Common Patterns

토큰 기본 함수는 typed dataclass나 분류된 토큰 예외를 반환한다. 세션 함수는 호출자 transaction을 사용하고 예상 보안 오류를 값으로 반환한다. HTTP 의존성만 `JsonApiException`으로 요청 오류를 전달한다.

## Dependencies

- 내부: [models](../models/AGENTS.md)의 User·RefreshSession, [JSON:API](../jsonapi/AGENTS.md) 오류 코드, [v1 controller](../controllers/api/v1/AGENTS.md), `config/auth.py`·`config/database.py`.
- 외부: pwdlib, PyJWT, FastAPI, SQLAlchemy 2와 PostgreSQL; hashlib·hmac·datetime·uuid 표준 라이브러리.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->

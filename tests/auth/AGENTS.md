<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# 인증 primitive와 세션 회귀 테스트

## 목적과 주요 파일

Argon2·JWT primitive, Bearer dependency와 PostgreSQL refresh session 상태 전이를 검증한다. 인증 endpoint 문서·HTTP 흐름은 상위 `test_auth_controller.py`와 `test_user_controller.py`에서도 확인한다.

| 파일 | 역할 |
| --- | --- |
| `test_passwords.py` | Argon2 hash·새 salt·정답/오답·dummy hash 검증 경로 |
| `test_tokens.py` | HS256·필수 claims·만료·issuer/audience/type·UUID jti, 만료 refresh 해석과 SHA-256·상수 시간 비교 |
| `test_dependencies.py` | 공유 앱의 Authorization 오류·삭제/비활성 사용자 조회, access/refresh 구별, endpoint 이전 인증 세션 종료 |
| `test_refresh_sessions.py` | 호출자 transaction, 회전·폐기·멱등 logout, 공통 세션 검증과 사용자별 잠금·동시 재사용 |

## 작업 규칙과 공통 패턴

- token 테스트의 secret·고정 UUID·시각은 테스트 데이터다. 실제 발급 수명과 decode의 현재 시각 검증을 구별하고, 만료 refresh 전용 decoder가 서명·issuer·audience·type 검증까지 우회하게 만들지 않는다.
- HTTP dependency 테스트는 공유 `app`·`client`·`persisted_user`·`access_token`·`auth_settings` fixture를 사용한다. 인증 세션을 계측할 때만 `app_factory(auth_session_factory_override=...)`로 factory를 주입한다. `get_auth_session_factory`가 반환한 factory의 세션은 endpoint 실행 전에 닫혀야 하므로 endpoint 안에서 종료 상태를 관찰한다.
- 세션 종료 후 detached User의 공개 응답이 유효해야 한다. `get_current_user`의 비활성 사용자 조회 성공과 활성 사용자만 허용하는 쓰기 권한을 혼동하지 않는다.
- JWT의 audience 배열과 문자열이 아닌 type claim도 명시적으로 거부한다. 잘못된 타입 입력을 위한 ignore는 해당 줄의 정확한 mypy 오류 코드에만 한정한다.
- refresh helper는 호출자의 transaction 안에서 변경을 준비한다. `Session.begin/commit/rollback`을 helper가 소유하지 않는 회귀를 유지하고, 오류 outcome과 DB 폐기 상태를 함께 확인한다.
- rotate와 logout은 없는 사용자·없는 jti·hash 불일치의 공통 검증을 적용한다. hash가 다른 행을 임의 폐기하지 않는지도 저장 상태로 확인한다.
- 같은 사용자의 재사용 감지는 진행 중인 발급·회전을 기다린 뒤 새 세션까지 폐기해야 한다. `Event`·별도 세션·`pg_backend_pid`·`pg_blocking_pids`로 실제 잠금 대기를 관찰한다.
- 다른 사용자는 잠금을 공유하지 않아야 한다. 경쟁 테스트의 해제 event는 `finally`에서 설정하고 future timeout을 유지한다.

## 검증

`TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리킬 때 `uv run pytest --no-cov tests/auth tests/test_auth_controller.py tests/test_user_controller.py -q`를 실행한다. URL이 없으면 `./scripts/check.sh`의 임시 Docker PostgreSQL 전체 게이트를 사용한다. mock으로 refresh 잠금·commit 회귀를 대체하지 않는다.

## 의존성

- 내부: `app/auth`, User·RefreshSession ORM, `config/auth.py`, `config/database.py`의 auth factory dependency, 상위 앱·commit·동시성 fixture.
- 외부: pytest, PyJWT, pwdlib Argon2, FastAPI·TestClient, SQLAlchemy·PostgreSQL, Python concurrent.futures·threading.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->

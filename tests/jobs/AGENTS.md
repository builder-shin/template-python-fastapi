<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# Dramatiq actor 테스트

## 목적과 주요 파일

Example 처리와 만료 refresh session 정리 actor의 입력·DB 동작·로그·재시도·worker 수명을 검증한다. 큐 전달은 StubBroker로 제어하고 모델 조회와 삭제·잠금은 실제 PostgreSQL에서 수행한다.

| 파일 | 역할 |
| --- | --- |
| `test_process_example.py` | actor 재시도 옵션, 정상 처리의 상태 불변성, 잘못된 UUID·없는 자원 로그, DB 오류 전파와 실제 Worker 재시도 |
| `test_purge_expired_refresh_sessions.py` | 보존 기간·반복 batch 삭제·로그, replacement 자기참조·User 보존, SKIP LOCKED와 FK cascade 대기 |

## 작업 규칙과 공통 패턴

- actor 모듈의 lazy `get_session_factory`를 test engine 기반 factory를 반환하도록 교체한다. Example actor는 실행할 때마다 factory를 해석하며, 잘못된 UUID는 세션을 열지 않고 없는 Example은 경고 후 정상 종료한다.
- 정상 처리와 반복 실행 후 공개 필드·관계·시각이 그대로인지 확인하고, `event`·`example_id`가 있는 log record를 검사한다. Alembic logging 설정의 영향을 제거하는 autouse logger fixture를 유지한다.
- `OperationalError`는 Dramatiq 재시도로 전달되어야 한다. 성공처럼 삼키거나 데이터 상태를 임의 변경하는 기대값을 만들지 않는다.
- 재시도 테스트는 StubBroker·Worker를 실제 실행한다. production 옵션(`max_retries=3`, `min_backoff=15000`) 검증과 테스트 중 짧게 설정한 옵션을 구별한다.
- `finally`에서 worker를 중지하고 actor options·actor broker·global broker를 복구하며 기존 broker에 actor를 다시 등록한다. consumer와 worker thread 종료도 단언한다.

## 만료 세션 정리

- 만료 시각이 보존 기간 경계보다 오래된 행만 삭제한다. revoked 여부만으로 삭제하지 않으며 유효한 세션·최근 만료 행은 남겨야 한다. 기본 604800초와 0·큰 보존 기간을 분리해 검사한다.
- 작은 batch를 반복해 짧은 batch에서 끝나는지 확인한다. 0 이하 batch 크기는 경고 후 세션 없이 0을 반환하고, 정상 삭제는 `refresh_sessions.purged`의 삭제 수를 기록한다.
- User를 보존하면서 오래된 replacement chain을 제거한다. 남아 있는 행의 대상이 삭제되면 `replaced_by_id`가 null이 되는 FK 계약도 실제 DB로 확인한다.
- `SKIP LOCKED`가 잠긴 후보를 건너뛰어도 자기참조 FK의 ON DELETE SET NULL은 대기할 수 있다. 별도 세션의 잠금·lock timeout·OperationalError와 `finally` 해제로 무한 대기를 방지한다.
- actor export와 재시도 옵션을 검증하되 자동 scheduler가 있다고 가정하지 않는다. 보존 기간 설정은 JWT 인증 설정과 독립된 계약이다.

## 검증

`TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리킬 때 `uv run pytest --no-cov tests/jobs tests/config/test_broker.py -q`를 실행한다. URL이 없으면 `./scripts/check.sh`의 임시 Docker PostgreSQL 전체 게이트를 사용한다. 이 경로의 큐 재시도는 StubBroker를 사용하며 운영 Redis 연결 검증은 아니다.

## 의존성

- 내부: `app/jobs/example.py`, `app/jobs/refresh_sessions.py`, `app/jobs/__init__.py`, Example·User·RefreshSession ORM, 상위 commit fixture, `config/database.py`·`config/auth.py`.
- 외부: pytest·caplog, Dramatiq Worker·StubBroker·Retries, SQLAlchemy·PostgreSQL.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->

<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# Dramatiq 작업 지침

## Purpose

worker가 명시 import하는 process_example·purge_expired_refresh_sessions actor를 정의한다. 예제 조회는 영속 상태를 바꾸지 않고, refresh 정리는 만료 후 보존기간을 지난 세션만 배치 삭제한다.

## Key Files

| 파일 | 역할 |
| --- | --- |
| `__init__.py` | broker 설정 후 두 actor import·export |
| `example.py` | UUID 검증·Example 조회·구조화 로그 |
| `refresh_sessions.py` | 오래된 refresh 세션의 잠금 제한·배치 삭제·삭제 건수 반환 |

## For AI Agents

### Working In This Directory

- actor 선언 전에 config.broker.configure_broker()가 실행되는 import 순서를 유지한다. Redis broker 설정은 config에 두고 새 actor는 이 패키지에서 명시 export한다.
- DB 접근은 config.database.get_session_factory()의 지연 생성된 worker/CLI용 factory를 사용한다. API factory의 engine이나 요청 Session을 작업에 전달하지 않는다.
- 두 actor의 retry는 max_retries=3·min_backoff=15_000ms다. DB operational error를 성공으로 삼키지 않고 Dramatiq에 전파한다. 자동 시드·서버 시작 시 enqueue·스케줄러를 추가하지 않는다.

### Example 작업

- process_example의 인자는 문자열 ID다. 잘못된 UUID는 session을 열기 전에 example.invalid_id warning, 없는 자원은 example.missing warning 후 종료한다.
- 정상 실행은 작업 내부 session으로 조회하고 example.processed·example_id를 로그로 남긴다. 반복 실행해도 Example 공개 상태를 변경하지 않는다.

### Refresh 보존기간 정리

- purge_expired_refresh_sessions는 기본 batch_size=1_000이며 0 이하이면 warning 후 DB 접근 없이 0을 반환한다.
- RefreshSessionRetentionSettings의 REFRESH_SESSION_RETENTION_SECONDS는 기본 604_800초이며 0 이상이다. JWT 설정과 분리되어 worker가 JWT_SECRET_KEY 없이 정리를 실행할 수 있다.
- 실행 시작에 cutoff를 고정하고 `expires_at < cutoff`인 행만 오래된 순서로 삭제한다. revoked_at만 보고 만료 전 세션을 지우지 않는다. expires_at 인덱스와 함께 검토한다.
- FOR UPDATE SKIP LOCKED로 직접 잠긴 행을 건너뛰고 RETURNING으로 삭제 건수를 센다. 배치마다 commit하고 마지막 배치가 batch_size보다 작으면 종료한다. 실패 전 완료한 배치는 유지된다.
- self FK의 ON DELETE SET NULL은 SKIP LOCKED 밖의 행을 잠글 수 있다. 각 배치 transaction에 `SET LOCAL lock_timeout = '2000ms'`를 적용해 cascade 대기를 제한하고 timeout은 actor retry로 넘긴다.
- 살아남은 사용자·만료 전 세션·후속 세션을 보존하고 삭제된 replacement를 가리키던 링크는 FK 정책으로 null 처리한다. 성공 로그는 refresh_sessions.purged·deleted·cutoff다.

### Testing Requirements

독립된 `*_test` PostgreSQL의 TEST_DATABASE_URL이 있을 때 `uv run pytest --no-cov tests/jobs tests/config/test_broker.py -q`를 실행하고, 없으면 `./scripts/check.sh`의 임시 Docker PostgreSQL 게이트를 사용한다.

- process_example: 잘못된 ID의 DB 미사용, 누락 자원, 반복 상태 불변, 실제 worker retry.
- purge: 만료·보존기간 경계, 배치 반복, rotation chain·다른 사용자 보존, FK cascade 잠금 timeout.
- StubBroker는 큐 검증용이고 DB는 실제 PostgreSQL을 유지한다. 테스트가 바꾼 actor options·broker·worker thread는 복원한다.

### Common Patterns

작업 인자는 직렬화 가능한 식별자·배치 설정이며 ORM 객체를 보내지 않는다. 작업 내부에서 session을 열고, 정리 작업의 commit은 HTTP 요청 transaction과 별개다.

## Dependencies

- 내부: [models](../models/AGENTS.md)의 Example·RefreshSession, config/database.py·config/broker.py·config/auth.py의 worker 설정.
- 외부: Dramatiq·Redis, SQLAlchemy 2와 PostgreSQL, logging·datetime·UUID.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->

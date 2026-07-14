# db migration 지침

## Alembic 환경

- migration URL은 `TEST_DATABASE_URL`, `DATABASE_URL`, `alembic.ini`의 순서로 선택하며, 어느 것도 없으면 즉시 실패한다.
- test URL이 설정되어 있으면 application URL이나 `alembic.ini` 값으로 조용히 fallback하지 않는다. migration이 의도하지 않은 데이터베이스를 대상으로 실행될 가능성을 숨기지 않는다.
- `target_metadata`는 `Base.metadata`이고, offline과 online 설정 모두 `compare_type=True`를 유지한다. online engine은 `NullPool`을 사용한다.
- migration environment가 모든 ORM 모델을 metadata에 포함하려면 `app.models`의 export 조립을 유지한다. revision에서만 모델을 import해 metadata 누락을 보완하지 않는다.
- offline 실행은 literal bind와 named paramstyle을 유지한다. online 실행은 짧은-lived connection을 transaction 안에서만 사용한다.

## revision 계약

- 이미 적용 가능한 historical revision은 수정하지 않는다. schema 변경은 새 revision으로만 전달한다.
- 새 revision은 Alembic template의 `revision`, `down_revision`, `branch_labels`, `depends_on` 선언을 보존한다. 독립적인 revision chain이나 숨은 branch를 임의로 만들지 않는다.
- 새 `upgrade`의 모든 변경은 `downgrade`에서 정확히 되돌릴 수 있어야 한다.
- PostgreSQL 타입, foreign key, constraint, index와 `ondelete` 정책은 migration에 명시한다.
- enum·association table·server default·named constraint의 생성과 제거 순서는 참조 무결성이 유지되도록 맞춘다. downgrade를 빈 `pass`로 두지 않는다.

## 작성과 검증

- model 변경만 커밋하거나 서버 시작 시 DDL을 실행하지 않는다. revision은 빈 PostgreSQL database에서 `head`까지 upgrade되는 유일한 배포 경로다.
- migration URL 선택과 실패 경로는 `tests/config/test_migrations.py`, 실제 빈 database upgrade와 table 구조는 `tests/integration/test_migration.py`가 보호한다.
- 새 head revision이면 `tests/config/test_migrations.py`의 head revision 기대값을 갱신한다. table을 추가·삭제할 때만 `tests/integration/test_migration.py`의 빈 database table-set 기대값을 갱신한다.
- migration round-trip은 먼저 `TEST_DATABASE_URL`을 독립된 `*_test` PostgreSQL URL로 export한 shell에서 실행한다. 그 뒤 `uv run alembic downgrade base && uv run alembic upgrade head && uv run alembic check`를 실행하고, 기존 head 데이터베이스 재적용도 검토한다. URL 없이 fail-closed한 명령을 개발 DB 대상으로 다시 실행하지 않는다.
- revision의 SQL을 PostgreSQL 이외의 방언 호환을 위해 약화하지 않는다. 이 서비스의 production contract는 PostgreSQL이다.

## 좁은 확인

```bash
uv run pytest --no-cov tests/config/test_migrations.py tests/integration/test_migration.py -q
```

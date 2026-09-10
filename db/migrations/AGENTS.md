<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# db/migrations Alembic 지침

## 목적

Alembic이 PostgreSQL에 접근할 URL과 ORM metadata를 선택하고, 동기식 online migration 또는 SQL 생성용 offline migration을 실행한다. 실제 스키마 이력은 `versions/`에 있으며, 이 디렉터리는 실행 환경과 새 revision의 형식을 소유한다.

## 주요 파일

| 파일 | 책임 |
| --- | --- |
| `env.py` | URL 우선순위, logging 초기화, `Base.metadata`, offline/online migration transaction을 구성한다. |
| `script.py.mako` | revision 설명·식별자·연결 정보와 `upgrade()`·`downgrade()`를 생성하는 Alembic template다. |

## 하위 디렉터리

| 경로 | 책임 |
| --- | --- |
| [versions/](versions/AGENTS.md) | Example·인증 자원과 정렬·만료 index의 순차 revision, PostgreSQL 타입·제약·index와 역방향 제거 순서. |

## 작업 지침

### Alembic 환경과 URL 선택

- `get_migration_database_url()`은 비어 있지 않은 `TEST_DATABASE_URL` → `DATABASE_URL` → `alembic.ini`의 `sqlalchemy.url` 순서로 선택한다. 빈 문자열은 다음 후보로 넘어가고, 어느 값도 없으면 `RuntimeError("Alembic database URL is required")`로 즉시 실패한다.
- 비어 있지 않은 test URL이 선택되면 연결 실패를 application URL이나 ini 값으로 조용히 fallback하지 않는다. 의도하지 않은 DB로 migration을 재시도하도록 바꾸지 않는다.
- 루트 `alembic.ini`의 기본 `sqlalchemy.url`은 비어 있다. 애플리케이션 DB 설정도 `DATABASE_URL`을 필수로 요구한다. migration에 로컬 PostgreSQL URL을 암묵적으로 추가하지 않는다.
- 이 환경 모듈 자체는 URL의 database 이름이 `_test`로 끝나는지 검증하지 않는다. 테스트·downgrade 검증 전 독립된 `*_test` PostgreSQL URL을 지정하는 규칙은 실행자가 지켜야 하며, DB 테스트 fixture는 이름을 검사한다.
- seed CLI는 `config.database.get_session_factory()`의 최초 호출에서 필수 `DATABASE_URL`을 읽어 지연 생성한 factory를 사용한다. migration의 `TEST_DATABASE_URL` 우선순위가 seed CLI에도 적용된다고 가정하지 않는다.
- `target_metadata`는 `app.models`에서 가져온 `Base.metadata`다. 모든 ORM 모델이 metadata에 등록되도록 `app.models` export 조립을 유지한다. revision에서만 모델을 import해 metadata 누락을 보완하지 않는다.
- offline과 online 설정 모두 `compare_type=True`를 유지한다. offline은 `literal_binds=True`와 named paramstyle을 사용하고 `context.begin_transaction()` 안에서 migration을 실행한다.
- online은 `engine_from_config(..., prefix="sqlalchemy.", poolclass=NullPool)`로 동기식 engine을 만들고, 짧게 유지하는 connection에서 migration transaction을 실행한다. 애플리케이션의 요청 pool이나 비동기 session을 공유하지 않는다.
- `config.config_file_name`이 있으면 해당 ini의 logging 설정을 `fileConfig`로 읽는다.

### revision 계약

- 이미 적용 가능한 historical revision은 수정하지 않는다. 모델이나 DB 스키마 변경은 새 revision으로만 전달한다.
- 새 revision은 template의 `revision`, `down_revision`, `branch_labels`, `depends_on` 선언을 보존한다. 독립적인 revision chain이나 숨은 branch를 임의로 만들지 않는다.
- 현재 chain은 `20260714_0001` → `20260715_0002` → `20260822_0003` → `20260822_0004`이며 마지막 revision이 head다. 구체적인 테이블·constraint는 [versions 지침](versions/AGENTS.md)을 따른다.
- 새 `upgrade()`의 스키마 변경은 `downgrade()`에서 되돌릴 수 있어야 한다. template가 생성한 빈 `pass`를 실제 변경의 downgrade로 남기지 않는다.
- PostgreSQL 타입, FK, constraint, index, `ondelete` 정책을 migration에 명시한다. enum·association table·server default·named constraint의 생성과 제거 순서는 참조 무결성이 유지되도록 맞춘다.
- 모델만 수정하거나 서버 시작 시 DDL을 실행하지 않는다. 빈 PostgreSQL database에서 revision을 `head`까지 적용하는 경로를 유지한다.
- revision SQL을 SQLite 등 다른 방언에 맞추기 위해 약화하지 않는다. 이 서비스의 운영 계약은 동기식 SQLAlchemy 2와 PostgreSQL이다.

### 변경 시 함께 확인할 항목

- 모델 export와 `Base.metadata`가 새 테이블·타입·관계를 포함하는지 확인한다.
- 새 head가 생기면 `tests/config/test_migrations.py`의 revision 기대값을 갱신한다.
- 테이블을 추가·삭제할 때만 `tests/integration/test_migration.py`의 빈 database table-set 기대값을 갱신한다. index 변경에는 해당 테스트의 index 존재·제거 단언도 확인하고, 중간 revision downgrade·재upgrade 검증을 유지한다.
- 빈 DB upgrade뿐 아니라 기존 head 데이터베이스에서 새 head 적용도 검토한다. 테스트 실패 시 대상 URL을 개발 DB로 바꾸어 재실행하지 않는다.

## 검증

아래 명령은 `TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL URL로 설정된 상태에서 실행한다. URL이 없으면 임의 DB를 선택하지 말고 `./scripts/check.sh`로 임시 Docker PostgreSQL 전체 게이트를 실행한다.

```bash
uv run pytest --no-cov tests/config/test_migrations.py tests/integration/test_migration.py -q
```

- `tests/config/test_migrations.py`는 application URL, test URL 우선순위, 명시적 ini URL, URL 누락 실패, revision head와 `alembic check` 결과를 검증한다.
- `tests/integration/test_migration.py`는 임시로 만든 빈 PostgreSQL database에 head를 적용하고 만료 index → 정렬 index → 인증 테이블 순서로 downgrade한 뒤 head를 다시 적용한다. 테스트 계정은 해당 임시 DB 생성·정리가 가능해야 한다.
- revision round-trip은 먼저 같은 shell에 독립된 `*_test` PostgreSQL의 `TEST_DATABASE_URL`을 export한 뒤 아래 순서로 실행한다. downgrade는 해당 테스트 DB 스키마를 제거하므로 운영·개발 데이터베이스에 실행하지 않는다.

```bash
uv run alembic downgrade base
uv run alembic upgrade head
uv run alembic check
```

- 정렬 index 변경은 `tests/integration/test_query_indexes.py`에서 실제 PostgreSQL index 정의와 기본 정렬의 EXPLAIN, cursor 위치 조건·반환 행을 확인한다. 좁은 실행은 `uv run pytest --no-cov tests/integration/test_query_indexes.py -q`다.
- DB 전반의 변경은 `uv run pytest --no-cov tests/config tests/integration tests/test_database_fixtures.py -q`로 확인한다.
- 최종 전체 게이트는 `uv sync --frozen` 후 `./scripts/check.sh`다. SQLite 단위 테스트나 애플리케이션 기동으로 migration 검증을 대신하지 않는다.

## 의존 관계

- 내부: 루트 `alembic.ini`, [app/models](../../app/models/AGENTS.md)의 export와 `Base.metadata`, [db 지침](../AGENTS.md)의 seed 실행 경계.
- 검증: `tests/config/test_migrations.py`, `tests/integration/test_migration.py`, `tests/integration/test_query_indexes.py`, `tests/conftest.py`의 Alembic head fixture.
- 외부: Alembic, SQLAlchemy 2, psycopg/PostgreSQL, Mako revision template.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->

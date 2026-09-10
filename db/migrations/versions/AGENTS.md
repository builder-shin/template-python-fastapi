<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# db/migrations/versions 스키마 이력 지침

## 목적

빈 PostgreSQL database에서 현재 ORM 저장 구조까지 도달하는 순차 Alembic revision을 보관한다. 각 파일이 타입·테이블·FK·constraint·index의 생성과 제거를 명시하며, 이미 존재하는 이력은 새 revision의 기준으로 유지한다.

## 주요 파일

| 파일 | revision·변경 |
| --- | --- |
| `20260714_0001_create_example_resources.py` | 최초 revision `20260714_0001`. `example_status` enum, `categories`·`tags`·`examples`·`example_tags`를 생성한다. |
| `20260715_0002_create_auth_resources.py` | `20260714_0001` 다음 revision `20260715_0002`. `users`·`refresh_sessions`와 관련 제약·index를 생성한다. |
| `20260822_0003_index_example_default_sort.py` | `20260715_0002` 다음 revision `20260822_0003`. Example 기본 정렬의 복합 index를 생성한다. |
| `20260822_0004_index_refresh_session_expiry.py` | `20260822_0003` 다음 revision `20260822_0004`; 현재 head. RefreshSession 만료 index를 생성한다. |

문서화할 하위 소스 디렉터리는 없다.

## 현재 스키마 계약

### Example revision

- `example_status`는 PostgreSQL enum이며 값은 `draft`, `active`, `archived`다. `create_type=False`로 선언하고 upgrade 시작에서 `create(..., checkfirst=True)`, 모든 참조 테이블을 제거한 뒤 `drop(..., checkfirst=True)`를 호출한다.
- `categories`와 `tags`는 UUID PK, 필수 `name`, 이름 unique constraint, timezone을 가진 생성·수정 timestamp를 갖는다.
- `examples`는 UUID PK, 필수 `title`·`status`·`score`, nullable `description`·`category_id`, timestamp를 갖는다. `score >= 0 AND score <= 100`은 DB check constraint다.
- `examples.category_id`는 `categories.id`를 참조하며 삭제 정책은 `SET NULL`이다. 해당 FK 열의 비고유 index를 유지한다.
- `example_tags`는 `(example_id, tag_id)` 복합 PK를 사용한다. 두 FK는 각각 `examples`와 `tags`를 참조하고 모두 `CASCADE` 삭제다.
- downgrade는 association → examples의 category_id index → examples → tags → categories → enum 순서로 참조 관계를 정리한다.

### 인증 revision

- `users`는 UUID PK, 필수·고유 `email`, 필수 `password_hash`, 서버 기본값 true의 `is_active`, 생성·수정 timestamp를 갖는다.
- `refresh_sessions`는 UUID PK, 필수 `user_id`·최대 64자의 `token_hash`·`expires_at`, nullable `revoked_at`·`replaced_by_id`, 생성 timestamp를 갖는다. `token_hash`는 고유하다.
- `user_id` FK는 `users.id`를 참조하고 사용자 삭제 시 `CASCADE`다. `replaced_by_id`는 같은 테이블의 `id`를 참조하며 `SET NULL`이다.
- `user_id`와 `replaced_by_id`에는 각각 비고유 index가 있다. downgrade는 두 index와 `refresh_sessions`를 먼저 지우고 `users`를 제거한다.
- 인증 revision을 내려도 앞선 Example 테이블은 남는다. 새 migration이 이 경계를 불필요하게 변경하지 않도록 확인한다.

### 정렬·만료 index revision

- `20260822_0003`은 `examples`의 비고유 복합 index `ix_examples_created_at_id`를 만든다. 열 순서와 방향은 `created_at DESC, id`이며 `id`는 기본 ASC다. downgrade는 이 index만 제거한다.
- Example 기본 정렬과 index의 열·방향을 함께 유지한다. 단일 timestamp index나 둘 다 ASC인 index로 바꾸면 현재 정렬 계약과 달라진다. `tests/integration/test_query_indexes.py`가 PostgreSQL index 정의와 실행 계획을 검사한다.
- `20260822_0004`는 `refresh_sessions.expires_at`의 비고유 index `ix_refresh_sessions_expires_at`를 만든다. 만료 시각으로 보존 기간이 지난 행을 조회하는 purge actor가 사용하는 검색 경로다. downgrade는 이 index만 제거한다.
- 두 index revision은 테이블을 추가하지 않는다. 새 테이블 집합을 기대하도록 테스트를 바꾸지 말고 index의 존재·제거와 모델 metadata 일치를 확인한다.

## 작업 지침

- 기존 revision을 편집해 변경을 배포하지 않는다. 모든 모델·DB 스키마 변경은 새 revision을 만들고 올바른 `down_revision`으로 현재 chain에 연결한다.
- `revision`, `down_revision`, `branch_labels`, `depends_on` 메타데이터를 보존한다. 현재 네 revision의 `branch_labels`와 `depends_on`은 모두 `None`이다.
- FK 대상은 먼저 생성하고 참조하는 table·index는 먼저 제거한다. enum은 참조 테이블보다 먼저 만들고 나중에 지운다.
- PostgreSQL UUID·ENUM, timezone timestamp, 명시적 server default, `op.f(...)`로 지정하는 제약·index 이름을 유지한다. SQLite 호환 코드를 넣지 않는다.
- 새 스키마 변경에는 실제 역방향 DDL을 작성한다. downgrade를 빈 `pass`로 두거나 모델 metadata 생성으로 migration을 대체하지 않는다.
- 새 모델은 [app/models](../../../app/models/AGENTS.md)의 export에도 연결해 Alembic `Base.metadata`가 이를 확인하게 한다. revision에서만 모델을 import하는 방식으로 metadata 누락을 보완하지 않는다.
- 새 head revision이면 `tests/config/test_migrations.py`의 revision 기대값을, 테이블 집합이 바뀌면 `tests/integration/test_migration.py`의 관련 기대값을 갱신한다.
- 시드는 revision 안에 자동 연결하지 않는다. 고정 예시 데이터는 [db seed 지침](../../AGENTS.md)의 명시적 명령과 호출자 transaction으로 관리한다.

## 검증

좁은 명령은 `TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL에 연결된 상태에서 실행한다. URL이 없으면 `./scripts/check.sh`의 임시 Docker PostgreSQL을 사용하고 개발 DB로 대신하지 않는다.

```bash
uv run pytest --no-cov tests/config/test_migrations.py tests/integration/test_migration.py -q
```

- 빈 DB에서 head까지 upgrade되는지, 기존 head에서 새 revision이 적용되는지, downgrade 후 다시 head까지 올라오는지 검토한다.
- `tests/integration/test_migration.py`는 head의 두 index 존재, `0003`·`0002`로 내려가며 각 index 제거, `0001`로 내려가며 인증 테이블 제거, head 재적용을 검증한다. `tests/config/test_migrations.py`는 전체 base/head 왕복과 `alembic check`로 모델과 migration의 차이를 확인한다.
- 정렬 index나 정렬 정책이 바뀌면 `uv run pytest --no-cov tests/integration/test_query_indexes.py -q`로 PostgreSQL index 정의·EXPLAIN·cursor 결과를 확인한다.
- 수동 base/head 왕복은 [상위 migration 지침](../AGENTS.md)의 URL 전제와 명령 순서를 따른다. Alembic 환경 자체가 `*_test` 이름을 강제한다고 가정하지 않는다.
- 최종 전체 검증은 `uv sync --frozen` 후 `./scripts/check.sh`다. migration·seed·동시성 동작은 실제 PostgreSQL 검증을 유지한다.

## 의존 관계

- 내부: [상위 migration 환경](../AGENTS.md)의 URL·metadata·transaction 구성, `script.py.mako`의 revision 형식, [app/models](../../../app/models/AGENTS.md)의 ORM 스키마.
- 검증: `tests/config/test_migrations.py`, `tests/integration/test_migration.py`, `tests/integration/test_query_indexes.py`, Alembic `head`를 적용하는 `tests/conftest.py`.
- 외부: Alembic `op`, SQLAlchemy 타입·DDL와 PostgreSQL 방언, psycopg/PostgreSQL.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->

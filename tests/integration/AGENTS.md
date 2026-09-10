<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# PostgreSQL 스키마·query·참조 자원 통합 테스트

## 목적과 주요 파일

빈 PostgreSQL의 migration, 호출자가 transaction을 소유하는 결정적 seed, 실제 query index 사용과 공개 참조 자원의 HTTP 계약을 검증한다.

| 파일 | 역할 |
| --- | --- |
| `test_migration.py` | 별도 임시 DB 생성, head upgrade·단계별 downgrade·재upgrade, 테이블·index 확인과 DB 정리 |
| `test_seed.py` | 고정 ID·관계·재실행 불변성, 변경된 seed 값 복구, 호출자 rollback과 자연키 충돌 |
| `test_query_indexes.py` | created_at/id index 정의·EXPLAIN, 동점 데이터의 양방향 keyset index 조건과 offset 결과 일치 |
| `test_reference_resources.py` | 공개 category/tag의 읽기 전용 route·name 정렬·필터·cursor·self link·거부 query |

## 작업 규칙과 공통 패턴

- migration 테스트는 검증된 test URL의 서버에 `fastapi_template_migration_<uuid>_test` DB를 만든다. DB 생성·삭제 권한이 필요하며 개발 DB를 대신 사용하지 않는다.
- schema는 Alembic으로만 생성한다. 새 모델·migration 추가 시 head의 테이블 집합과 downgrade 후 기대 집합을 함께 검토한다.
- head의 `ix_examples_created_at_id`·`ix_refresh_sessions_expires_at`를 확인하고, 각각 해당 index 이전 revision으로 내렸을 때 사라지는지 검사한다. 인증 테이블 제거·재생성도 별도 downgrade 경계로 유지한다.
- 임시 DB 이름은 식별자로 quote하고, `finally`에서 해당 engine을 dispose한 뒤 생성했던 DB만 제거한다. admin engine의 수명도 정리한다.
- seed의 두 번째 실행은 개수·관계뿐 아니라 변경 없는 행의 `updated_at`도 유지해야 한다. 기존 고정 ID 행의 값이 달라진 경우에는 값·관계를 복구하고 시각을 갱신하는지 검사한다.
- seed가 직접 commit하지 않는지 caller rollback으로 확인한다. 다른 ID가 같은 자연키를 차지한 경우 `IntegrityError`를 숨기지 않고 테스트 세션을 rollback한다.
- seed CLI는 `get_session_factory`를 주입해 factory가 여는 transaction의 commit과 세션 종료를 확인한다. `seed(session)`의 호출자 소유 transaction과 CLI 진입점의 책임을 구별한다.
- EXPLAIN 회귀는 `SET LOCAL enable_seqscan=off`와 index 정의로 정렬용 index·Sort 부재를 확인한다. cursor 사례는 동점 행과 `ANALYZE` 후 양방향 keyset 조건이 선두 `created_at`의 Index Cond에 포함되는지 보며, 단순 Filter 통과만으로 대체하지 않는다.
- 참조 자원은 공유 `client`와 committed 데이터로 실제 공개 route를 호출한다. name 정렬 fixture는 DB collation 차이를 피할 ASCII 이름을 쓰고, cursor 전체 순회·필터·자원 self link와 미지원 쓰기의 405를 확인한다.

## 검증

독립된 `*_test` PostgreSQL의 `TEST_DATABASE_URL`을 준비하고 `uv run pytest --no-cov tests/config tests/integration tests/test_database_fixtures.py -q`를 실행한다. URL이 없으면 `./scripts/check.sh`가 만드는 임시 Docker PostgreSQL 전체 게이트를 사용한다. SQLite·metadata 생성으로 대체하지 않는다.

## 의존성

- 내부: 상위 앱·DB fixture, `alembic.ini`, `db/migrations`, `db/seeds.py`의 고정 식별자, Example·category·tag ORM, `app/jsonapi/query.py`와 참조 자원 controller·serializer.
- 외부: pytest, Alembic, SQLAlchemy·psycopg와 PostgreSQL의 CREATE/DROP DATABASE.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->

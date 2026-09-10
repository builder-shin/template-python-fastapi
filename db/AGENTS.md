<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# db 데이터 운용 지침

## 목적

PostgreSQL 스키마를 배포하는 Alembic migration과 개발용 결정적 seed를 관리한다. 스키마 생성은 revision, 예시 데이터 작성은 명시적 seed 명령으로 나누며, 애플리케이션 시작 경로에서 데이터나 테이블을 자동으로 만들지 않는다.

## 주요 파일

| 파일 | 책임 |
| --- | --- |
| `seeds.py` | 고정 UUID의 category·tag·example·association을 PostgreSQL upsert로 맞추는 `seed(session)`과 CLI `main()`을 제공한다. |

## 하위 디렉터리

| 경로 | 책임 |
| --- | --- |
| [migrations/](migrations/AGENTS.md) | Alembic URL 선택, ORM metadata 연결, revision template와 upgrade/downgrade 실행. |

## 작업 지침

### 결정적 시드

- `seed(session)`은 호출자가 소유한 transaction 안에서만 데이터를 변경하며 commit하지 않는다. 명령행 진입점은 `get_session_factory().begin()`으로 transaction을 연다.
- CLI 시드는 `uv run python -m db.seeds`로 명시 실행한다. `create_app`, ASGI import, Docker 서비스 시작 경로에서 자동으로 호출하지 않는다.
- CLI는 `config.database.get_session_factory()`를 명시적으로 호출한다. 이 함수는 최초 호출 시 필수 `DATABASE_URL`을 읽어 engine·session factory를 만들고 프로세스 단위로 캐시한다. import만으로 engine을 만들지 않는다. `TEST_DATABASE_URL`만 지정해도 seed CLI의 실행 대상이 바뀌는 것은 아니다. 명령 실행 전 의도한 대상에 `DATABASE_URL`이 연결되어 있는지 확인한다.
- 고정 UUID 행은 PostgreSQL `INSERT ... ON CONFLICT DO UPDATE`로 맞춘다. 값이 달라질 때만 `is_distinct_from` 조건으로 갱신해 재실행 시 `updated_at`이 불필요하게 변하지 않게 한다.
- `SEED_CATEGORY_ID`, `SEED_TAG_ID`, `SEED_EXAMPLE_ID`는 기존 개발·계약 테스트 데이터의 stable identifier다. 같은 목적의 행을 임의 UUID로 중복 생성하지 않는다.
- category → tag → example → association 순서로 하나의 호출자 transaction에서 하나의 graph를 맞춘다. 일부 노드만 먼저 commit하거나 seed 내부에서 transaction을 중첩하지 않는다.
- association은 `(example_id, tag_id)` 충돌 시 무시해 중복을 만들지 않는다. 현재 seed는 해당 연결을 보장하며 다른 기존 tag 연결을 삭제하지 않는다.
- association conflict 처리와 자연 키 unique 충돌을 혼동하지 않는다. 다른 ID의 category·tag가 같은 이름을 점유하면 `IntegrityError`를 숨기거나 덮어쓰지 않는다.

### 현재 seed graph

| 식별자 | 고정 ID | 데이터 |
| --- | --- | --- |
| `SEED_CATEGORY_ID` | `00000000-0000-4000-8000-000000000001` | 이름 `기본 카테고리`. |
| `SEED_TAG_ID` | `00000000-0000-4000-8000-000000000002` | 이름 `기본 태그`. |
| `SEED_EXAMPLE_ID` | `00000000-0000-4000-8000-000000000003` | 제목 `JSON:API 예시`, 고정 설명, `ACTIVE` 상태, score `90`, 위 category와 tag 연결. |

인증용 `User`나 `RefreshSession`은 현재 seed에 포함되지 않는다. 새 모델을 seed에 추가하면 model import, Alembic migration, FK에 따른 생성 순서를 함께 검토한다. migration 전 테이블이 존재한다고 가정하지 않는다.

### 변경과 transaction 계약

- seed 값을 변경하면 두 번 실행한 뒤 행 수·고정 ID·관계·`updated_at` 안정성을 함께 검증한다.
- drift를 고칠 때는 고정 ID 행만 upsert한다. 이름 같은 자연 키로 다른 사용자의 행을 흡수하지 않는다.
- nullable 속성을 비교할 때 기존 `is_distinct_from` 방식을 유지한다. example은 여러 속성 중 하나라도 다를 때 `or_` 조건으로 갱신한다.
- transaction 테스트에서는 `seed(session)` 뒤 호출자의 commit/rollback 선택이 그대로 유지되는지 확인한다. seed 함수 안의 implicit commit은 금지한다.
- SQLAlchemy 2의 동기식 `Session`과 PostgreSQL 방언을 유지한다. DB 동작을 SQLite 대체 구현으로 검증하거나 repository/service 계층을 만들지 않는다.
- 모델·DB 스키마 변경은 [migration 지침](migrations/AGENTS.md)에 따라 새 Alembic revision으로 전달한다. `Base.metadata.create_all`이나 서버 시작 시 DDL로 대체하지 않는다.
- 운영 compose는 seed를 시작 단계에 포함하지 않는다. 데이터가 필요한 환경에서 명시 명령으로만 실행한다.

## 검증

좁은 pytest 명령은 `TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리킬 때 실행한다. URL이 없으면 임의 DB로 대체하지 말고 `./scripts/check.sh`를 사용한다. seed SQL은 Alembic `head`가 적용된 PostgreSQL fixture에서 검증하며, metadata 생성이나 SQLite 테스트로 대체하지 않는다.

```bash
uv run pytest --no-cov tests/integration/test_seed.py -q
```

- `tests/integration/test_seed.py`는 재실행의 행 수·timestamp 안정성, 고정 ID drift 복원, 호출자 rollback, 자연 키 unique 충돌을 검증한다.
- commit 결과를 보는 seed 테스트는 `committed_session` fixture를 사용하고 fixture의 전후 정리를 유지한다.
- migration·seed·세션이 함께 바뀌면 `uv run pytest --no-cov tests/config tests/integration tests/test_database_fixtures.py -q`로 관련 경계를 확인한다.
- 최종 전체 게이트는 `uv sync --frozen` 후 `./scripts/check.sh`다. 이 스크립트는 필요한 경우 임시 Docker PostgreSQL을 만들고 Ruff·mypy·pytest·detect-secrets를 실행한다.

## 의존 관계

- 내부: [app/models](../app/models/AGENTS.md)의 `Example`·`ExampleCategory`·`ExampleTag`·`ExampleStatus`·`example_tags`, [config](../config/AGENTS.md)의 `get_session_factory()`.
- 검증: `tests/integration/test_seed.py`, `tests/conftest.py`의 migration·transaction fixture.
- 외부: SQLAlchemy 2의 PostgreSQL `insert`와 `func`·`or_`, psycopg/PostgreSQL, Alembic.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->

# db 데이터 운용 지침

## 결정적 시드

- `seed(session)`은 호출자가 소유한 transaction 안에서만 데이터를 변경하며, commit하지 않는다. 명령행 진입점은 `get_session_factory().begin()`으로 transaction을 연다.
- CLI 시드는 `uv run python -m db.seeds`로 명시 실행한다. `create_app`, ASGI import, Docker 서비스 시작 경로에서 자동으로 호출하지 않는다.
- 고정 UUID 행은 PostgreSQL upsert로 맞춘다. 값이 달라질 때만 `is_distinct_from` 조건으로 갱신해 재실행 시 timestamp가 불필요하게 변하지 않게 한다.
- `SEED_CATEGORY_ID`, `SEED_TAG_ID`, `SEED_EXAMPLE_ID`는 기존 개발·계약 테스트 데이터의 stable identifier다. 같은 목적의 행을 임의 UUID로 중복 생성하지 않는다.
- category, tag, example과 association은 하나의 호출자 transaction 안에서 하나의 graph로 맞춘다. 일부 노드만 먼저 commit하거나 seed 내부에서 transaction을 중첩하지 않는다.
- association 행은 충돌 시 무시해 중복을 만들지 않는다.
- association conflict 처리와 자연 키 unique 충돌을 혼동하지 않는다. 후자는 숨기거나 덮어쓰지 않고 `IntegrityError`로 드러나야 한다.

## 변경 규칙

- seed 내용을 바꿀 때는 두 번 실행한 뒤 행 수·고정 ID·관계·`updated_at` 안정성을 함께 검증한다.
- 의도적으로 drift를 고치는 경우에는 고정 ID 행만 upsert하고, 이름 같은 자연 키로 다른 사용자의 행을 흡수하지 않는다.
- transaction 테스트에서는 `seed(session)` 뒤 호출자의 commit/rollback 선택이 그대로 유지되는지 확인한다. seed 함수 안의 implicit commit은 금지한다.
- seed가 새 모델을 참조하면 해당 model import, Alembic migration, 생성 순서를 함께 검토한다. migration 전 테이블이 존재한다고 가정하지 않는다.
- seed 테스트는 Alembic `head`가 적용된 PostgreSQL fixture에서 실행한다. metadata 생성이나 SQLite 대체로 seed SQL을 검증하지 않는다.

## 관련 경계

- schema 배포와 revision 작성은 `migrations/AGENTS.md`를 따른다. 이 문서는 migration 생성 도구나 `Base.metadata.create_all`을 사용하지 않는다.
- 운영 compose는 seed를 시작 단계에 포함하지 않는다. 사용자가 필요한 환경에서만 명시 명령으로 실행한다.

## 좁은 확인

```bash
uv run pytest --no-cov tests/integration/test_seed.py -q
```

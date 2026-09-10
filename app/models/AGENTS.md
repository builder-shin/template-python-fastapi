<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# ORM 모델 지침

## Purpose

SQLAlchemy 2의 저장 구조·제약조건·FK·관계를 정의한다. 공개 입력은 schema, 공개 응답 필드는 serializer가 결정하며, 모델 변경은 Alembic migration으로 전달한다.

## Key Files

| 파일 | 역할 |
| --- | --- |
| `__init__.py` | Base와 모든 모델·ExampleStatus·example_tags export 및 metadata 등록 |
| `base.py` | 결정적 constraint naming convention, Base, 시간대 있는 TimestampMixin |
| `example.py` | Example, enum·score check, created_at DESC/id 기본 정렬 인덱스, example_tags |
| `example_category.py` | 고유 name의 categories와 examples 역관계 |
| `example_tag.py` | 고유 name의 tags와 examples 다대다 역관계 |
| `user.py` | UUID 사용자, 고유 email, password_hash, 기본 활성 상태와 refresh 관계 |
| `refresh_session.py` | refresh JWT digest·만료 인덱스·폐기 시각·후속 세션 연결 |

## For AI Agents

### Working In This Directory

- 새 모델은 `Base`를 상속하고 `__init__.py`에서 import한다. 기존 UUID·timezone timestamp·naming convention을 재사용하고 단일 자원용 base/repository/service 계층을 추가하지 않는다.
- 테이블·column·enum·constraint·FK·nullability 변경에는 새 Alembic migration을 반드시 포함한다. 시작 시 DDL, 테스트 전용 create_all, SQLite 호환 분기를 추가하지 않는다.
- QueryPolicy filter·sort·기본 순서·tie breaker를 바꾸면 인덱스 필요성을 함께 판단한다. `ix_examples_created_at_id`는 `(created_at DESC, id ASC)`의 혼합 방향을 그대로 유지하고 모델·migration을 함께 바꾼다. 인덱스를 만들지 않으면 정책 선언부에 이유를 남긴다.
- ExampleStatus는 enum 이름이 아니라 `draft/active/archived` 값을 PostgreSQL enum에 저장한다. score의 0–100 DB check와 쓰기 schema 범위를 함께 유지한다.
- category 삭제는 Example.category_id를 SET NULL로, Example/tag 삭제는 연결 행을 CASCADE로 처리한다. 공유 category·tag를 Example 삭제와 함께 지우는 ORM cascade를 추가하지 않는다.
- `example_tags`는 example_id·tag_id 복합 PK로 중복 연결을 막는다. 관계 cardinality·back_populates와 serializer 선언을 일치시킨다.
- 사용자 이메일 정규화는 schema의 `normalize_email`을 호출하는 controller 책임이다. 모델만으로 모든 저장이 정규화된다고 가정하지 않는다. 비밀번호 원문과 raw refresh token을 저장하지 않는다.
- RefreshSession.id는 발급된 refresh JWT의 jti이며 token_hash는 고유한 64자 digest 저장 열이다. user 삭제는 세션을 CASCADE, 후속 세션 삭제는 replaced_by_id를 SET NULL로 처리한다.
- refresh session의 expires_at 인덱스는 보존기간이 지난 세션을 오래된 순서로 정리하는 actor를 지원한다. 폐기 여부만으로 아직 만료되지 않은 세션을 삭제하지 않는다.
- created_at은 DB 기본값, TimestampMixin.updated_at은 SQLAlchemy onupdate를 사용한다. Core upsert의 갱신 시각은 공통 CrudActions가 별도로 처리하므로 모델 변경 시 함께 검토한다.

### Testing Requirements

`TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리킬 때 `uv run pytest --no-cov tests/models tests/config/test_migrations.py tests/integration/test_migration.py tests/integration/test_query_indexes.py -q`를 실행한다. URL이 없으면 `./scripts/check.sh`의 임시 Docker PostgreSQL 게이트를 사용한다. 새 head revision과 테이블 집합의 기대값을 갱신하고 upgrade·downgrade, FK 삭제·고유성·check를 실제 PostgreSQL로 확인한다.

### Common Patterns

`Mapped[T]`·`mapped_column()`·`relationship()`을 사용한다. 순환 type hint는 TYPE_CHECKING으로 해결하고 runtime 모델 import는 metadata 조립점에서 유지한다. nullable 저장 필드와 공개 관계 linkage는 별개 선언이다.

## Dependencies

- 내부: [migration](../../db/migrations/AGENTS.md), [schemas](../schemas/AGENTS.md), [serializers](../serializers/AGENTS.md), [auth](../auth/AGENTS.md), `config/database.py`.
- 외부: SQLAlchemy 2와 PostgreSQL UUID·enum·FK, Python uuid·datetime·StrEnum.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->

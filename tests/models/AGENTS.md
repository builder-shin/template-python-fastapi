<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# ORM 저장 계약 테스트

## 목적과 주요 파일

Alembic migration을 적용한 PostgreSQL에서 모델의 기본값·제약·관계·cascade를 검증한다. 외부 응답 필드와 입력 검증은 serializer·schema 테스트가 담당한다.

| 파일 | 역할 |
| --- | --- |
| `test_example.py` | score 범위, UUID·시각, category/tag 관계·이름 unique, association 복합 PK와 삭제 cascade |
| `test_user.py` | UUID·활성 기본값·UTC 시각, 이메일 unique·길이, 필수 password hash와 이메일 저장 |
| `test_refresh_session.py` | UUID jti·시각·폐기 초기값, token hash unique·최대 길이, replacement 자기참조와 사용자 삭제 cascade |
| `__init__.py` | 모델 테스트 패키지 경계 |

## 작업 규칙과 공통 패턴

- `db_session`에 모델을 추가하고 `flush()`하여 DB 기본값과 제약을 실제 적용한다. `IntegrityError`와 PostgreSQL 길이 초과 `DataError`를 schema validation 오류로 대체하지 않는다.
- migration·model 변경은 대응 제약 테스트와 함께 검토한다. `Base.metadata.create_all()`이나 SQLite 분기로 migration 경로를 건너뛰지 않는다.
- Example 삭제 후 association만 없어지고 tag 자체는 남는지, User 삭제 후 refresh session이 제거되는지 각각 확인한다.
- User 모델의 정규화된 문자열 저장 테스트가 이메일 입력 정규화를 구현하거나 보장한다고 해석하지 않는다. 정규화 로직은 schema·controller 회귀도 확인한다.
- token hash의 unique·최대 64자 제한과 실제 SHA-256 생성은 서로 다른 계약이다. refresh 회전·폐기 동시성은 `tests/auth`에서 검증한다.
- 정렬·만료 정리 index의 생성·제거와 실제 사용 계획은 `tests/integration`에서 확인한다. refresh replacement 삭제의 SET NULL과 잠금 대기는 `tests/jobs/test_purge_expired_refresh_sessions.py`의 회귀도 함께 검토한다.
- 잘못된 값을 넣는 제약 테스트도 strict mypy의 검사 대상이다. SQLAlchemy가 선언한 입력 타입을 확인하고 불필요한 ignore를 추가하지 않는다.

## 검증

독립된 `*_test` PostgreSQL을 가리키는 `TEST_DATABASE_URL`을 준비하고 `uv run pytest --no-cov tests/models -q`를 실행한다. URL이 없으면 `./scripts/check.sh`가 임시 Docker PostgreSQL을 준비하는 전체 게이트를 사용한다. DB schema 변경은 `tests/integration` migration 회귀도 확인한다.

## 의존성

- 내부: `app/models`, `app/auth/passwords.py`, 상위 `db_session`과 Alembic fixture, `db/migrations`.
- 외부: pytest, SQLAlchemy·psycopg·PostgreSQL, Argon2 hash primitive.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->

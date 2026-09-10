<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# API 애플리케이션 계층 지침

## Purpose

`app/`는 SQLAlchemy 모델, 쓰기 schema, 공개 serializer, controller, JSON:API 프로토콜, 인증과 백그라운드 작업을 담는다. 모델은 저장 구조, schema는 입력 검증·조회 allowlist, serializer는 공개 표현, controller는 HTTP 액션과 transaction을 소유한다. 공통 CRUD와 프로토콜 구현을 자원 controller에 복사하거나 repository/service 계층을 추가하지 않는다.

## Key Files

| 파일 | 역할 |
| --- | --- |
| `__init__.py` | 애플리케이션 도메인 패키지 표시; 라우트·시드 자동 등록 없음 |

## Subdirectories

| 경로 | 책임 |
| --- | --- |
| [auth/](auth/AGENTS.md) | Argon2, JWT, bearer 의존성과 refresh session 회전 |
| [controllers/](controllers/AGENTS.md) | 선언형 자원 액션, 명시 인증·현재 사용자·health 경로 |
| [jobs/](jobs/AGENTS.md) | Dramatiq actor와 명시 export |
| [jsonapi/](jsonapi/AGENTS.md) | 문서, 협상, query 파싱, 다국어 오류와 응답 |
| [models/](models/AGENTS.md) | 테이블·제약조건·FK·ORM 관계 |
| [schemas/](schemas/AGENTS.md) | Pydantic 쓰기 입력과 QueryPolicy |
| [serializers/](serializers/AGENTS.md) | JSON:API type·attributes·relationships·include 로더 |

## For AI Agents

### Working In This Directory

- 모든 controller는 `JsonApiController`를 상속한다. 자원은 `CrudActions`를 통해, 인증·사용자·health는 직접 상속하며 router 조립·prefix 검증·Accept·Content-Type 계약을 복사하지 않는다.
- 새 자원은 모델·관계 → Alembic migration → write/relationship schema·QueryPolicy → serializer → 얇은 `CrudActions` controller → `config/routes.py` → 테스트 순서로 연결한다.
- 모델은 `models/__init__.py`에서 import해 metadata에 포함한다. 필요한 public symbol은 `schemas/__init__.py`, `serializers/__init__.py`, `controllers/api/v1/__init__.py`에도 export한다. route 등록은 `config/routes.py`에서 명시적으로 한다.
- nullability, cascade, FK 삭제 정책, enum·제약조건 변경은 모델과 migration에 함께 반영한다. PostgreSQL 전용 기능을 SQLite 분기, 시작 시 DDL, 테스트 전용 테이블 생성으로 대체하지 않는다. 기존 `Base`·`TimestampMixin`을 우선 사용한다.
- schema는 `app/jsonapi/naming.py`의 `JsonApiWriteSchema`를 상속해 strict·extra 금지·camelCase 설정을 공유한다. 자체 ConfigDict·StrictStr·이름 변환 함수를 다시 만들지 않고 create/update/replace의 필수성 차이를 유지한다. PATCH의 `MISSING`은 생략과 명시적 null을 구분하며, PUT은 완전한 attributes를 받는다. 관계는 public linkage 입력으로 받고 내부 FK를 직접 공개하지 않는다.
- `QueryPolicy`에 filter 연산자·엄격한 값 parser·sort·include·기본 정렬·결정적 tie breaker를 선언한다. 임의 SQL 열·관계 경로, 새 query parameter, `fields[...]` 희소 필드셋을 추가하지 않는다.
- QueryPolicy filter·sort·기본 순서·tie breaker 변경은 인덱스 필요성을 판단하고 모델·migration에 함께 반영한다. 만들지 않으면 정책 선언부에 근거를 남긴다.
- 공개 필드와 관계는 serializer에서만 선택한다. `RelationshipDefinition`에 대상 serializer와 cardinality를 선언하고 include는 serializer와 policy 양쪽에서 허용한다. route 안의 지연 로딩으로 serializer loader를 우회하지 않는다.
- 자원 serializer의 `resource_path`와 controller prefix를 함께 검토한다. resource·relationship link와 POST/PUT `Location`의 기준이다. 현재 사용자 serializer는 `resource_location()`으로 고정 `/api/v1/users/me`를 반환한다.
- 쓰기 가능한 CRUD controller는 model·serializer·create/update/replace schema·관계 schema·query policy를 선언한다. `enable_upsert=True`는 PostgreSQL PUT 계약이 필요한 경우만 사용한다. 서버 관리 참조 데이터는 `enable_writes=False`로 모든 쓰기·관계 mutation route를 생략하고 세 write schema도 선언하지 않는다. 제네릭 인자는 `CrudActions[Model, BaseModel, BaseModel, BaseModel]`을 사용한다.
- 읽기 scope·strong params·관계 할당·lifecycle hook으로 도메인 규칙을 확장한다. hook으로 표현되지 않을 때만 메서드를 재정의하고, 별도 commit·외부 I/O로 transaction rollback을 깨지 않는다.
- 성공·오류 문서는 `JsonApiResponse`와 JSON:API 문서 모델로 반환하고 안전한 `JsonApiException`을 전역 handler로 전달한다. 기존 빈 204와 health의 Accept 생략은 각 controller의 한정된 계약이며 새 일반 JSON 응답의 근거가 아니다.
- 인증 조회는 `get_auth_session_factory`에서 연 짧은 session에서 User를 읽고 expunge·close한 뒤 detached User를 반환한다. endpoint는 `get_request_session`으로 별도 요청 session을 연결하므로 인증 조회가 요청 내내 풀 연결을 점유하지 않는다. refresh 발급·회전·폐기는 호출자 소유 transaction과 사용자별 잠금 순서를 유지한다. 자세한 동시성·replay 계약은 [auth 지침](auth/AGENTS.md)을 따른다.

### Testing Requirements

아래 좁은 pytest는 `TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리킬 때만 실행한다. URL이 없으면 임의 DB로 대체하지 말고 `./scripts/check.sh`의 임시 Docker PostgreSQL 전체 게이트를 사용한다.

| 변경 | 확인 |
| --- | --- |
| 새 자원·공개 route | `uv run pytest --no-cov tests/models tests/serializers tests/controllers tests/test_example_controller.py tests/integration/test_reference_resources.py -q`; 새 자원은 `tests/test_<resource>_controller.py` 추가 |
| 인증·사용자 | `uv run pytest --no-cov tests/auth tests/schemas tests/test_auth_controller.py tests/test_user_controller.py -q` |
| migration·세션 | `uv run pytest --no-cov tests/config tests/integration tests/test_database_fixtures.py -q` |
| 프로토콜 | `uv run pytest --no-cov tests/jsonapi -q` |
| actor | `uv run pytest --no-cov tests/jobs -q` |

- 새 revision이면 migration head 기대값, 테이블 추가·삭제면 빈 DB table-set 기대값도 갱신한다.
- 공개 route는 `create_app()` 기반 테스트에서 실제 조립·OpenAPI·성공·거부 응답을 검증하고, 관계·upsert·rollback·인증 경쟁은 실제 PostgreSQL fixture로 검증한다.
- API 변경의 최종 게이트는 `uv sync --frozen` 후 `./scripts/check.sh`다. 테스트 상세는 [tests 지침](../tests/AGENTS.md)을 따른다.

### Common Patterns

`models`는 저장 enum·관계를, `schemas`는 입력과 QueryPolicy를, `serializers`는 camelCase 공개 표현을 제공한다. `ExamplesController`는 선언만으로 공통 CRUD를 상속하고 쓰기 인증을 `write_dependencies`에 연결한다. Categories·Tags는 읽기 전용 참조 자원이며 canonical 링크를 갖는다. 공통 CRUD는 concerns의 선언·문서 파싱·route·관계·upsert·액션 모듈로 나뉘고 자원 controller의 공개 진입점은 CrudActions다. AuthController·UsersController·HealthController는 JsonApiController를 직접 상속한다.

## Dependencies

- 내부: [config](../config/AGENTS.md)의 factory·route·Session·JWT·broker 조립, [db](../db/AGENTS.md)의 migration·명시 seed, [tests](../tests/AGENTS.md)의 PostgreSQL fixture.
- 외부: FastAPI/Starlette, SQLAlchemy 2와 PostgreSQL, Pydantic, PyJWT, pwdlib, Dramatiq.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->

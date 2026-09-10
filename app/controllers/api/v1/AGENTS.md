<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# V1 API Controller 지침

## Purpose

`/api/v1` 공개 API의 자원 선언과 인증 흐름을 담당한다. CRUD 자원은 공통 concern을 상속하고, 계정 등록·로그인·refresh·logout·현재 사용자 조회는 각 명시 액션의 계약을 유지한다.

## Key Files

| 파일 | 역할 |
| --- | --- |
| `__init__.py` | Auth·Examples·ExampleCategories·ExampleTags·Users controller public export |
| `examples_controller.py` | Example 모델·schema·serializer·query policy, PUT upsert와 쓰기 인증 선언 |
| `example_categories_controller.py` | `/api/v1/categories` GET 목록·단일 조회, write schema 없는 참조 자원 |
| `example_tags_controller.py` | `/api/v1/tags` GET 목록·단일 조회, write schema 없는 참조 자원 |
| `auth_controller.py` | register/login/refresh/logout route와 호출자 소유 transaction |
| `users_controller.py` | bearer 기반 현재 사용자 `/me` JSON:API 표현 |

## For AI Agents

### Working In This Directory

- 모든 controller는 JsonApiController를 상속한다. 새 자원은 `CrudActions`와 model·serializer·query policy로 선언하고, 쓰기 가능할 때 create/update/replace·관계 schema를 추가한다. public export와 `config/routes.py`의 명시 router 조립을 함께 갱신한다.
- ExamplesController는 공개 읽기와 `get_current_active_user`가 필요한 모든 쓰기를 제공한다. 자원 쓰기뿐 아니라 관계 mutation에도 `write_dependencies`가 적용되는 계약을 유지한다.
- ExampleCategoriesController·ExampleTagsController는 `enable_writes=False`로 GET만 등록하며 세 write schema를 생략한다. type은 exampleCategories/exampleTags, canonical 경로는 categories/tags이며 기본 정렬은 name·id 오름차순이다. 역관계 include는 열지 않는다.
- AuthController는 base의 JSON:API Accept·Content-Type 검증과 공용 `jsonapi_error_responses()`를 사용한다. session은 `get_request_session`으로 연결한다. register는 정규화한 이메일·Argon2 hash를 같은 transaction에 저장하고 201 사용자 문서와 `Location: /api/v1/users/me`를 반환한다.
- 이메일 중복은 이름이 `uq_users_email`인 제약 위반만 409 `EMAIL_ALREADY_REGISTERED`로 바꾼다. 나머지 IntegrityError는 전역 handler로 전달한다.
- login은 존재하지 않는 이메일에도 dummy hash를 검증한다. 비밀번호 확인 후 사용자 행을 잠그고 활성 상태를 다시 확인한 뒤 토큰과 refresh session을 같은 transaction에 발급한다.
- refresh/logout은 `RefreshSessionError` 결과를 transaction 종료 뒤 HTTP 오류로 변환한다. 만료·replay·비활성 계정에 따른 폐기 기록이 오류 때문에 rollback되지 않게 한다.
- logout은 유효기간 내 반복 요청에 빈 204를 반환한다. 이 기존 빈 응답 계약과 JSON:API 오류 응답을 유지한다.
- UsersController의 `/me`는 `get_current_user`가 짧은 조회 session을 닫고 반환한 detached User를 사용하며 활성 계정 전용 의존성이 아니다. UserSerializer의 공개 필드와 고정 self URL을 사용하고 비밀번호·refresh session을 노출하지 않는다.
- schema는 입력, serializer는 공개 표현, auth 함수는 토큰·잠금을 소유한다. controller에 공통 기능이나 번역 문자열을 복사하지 않는다.

### Testing Requirements

`TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리킬 때 다음을 실행한다. URL이 없으면 `./scripts/check.sh`의 임시 Docker PostgreSQL 게이트를 사용한다.

- 자원·관계·upsert·참조 데이터: `uv run pytest --no-cov tests/controllers tests/test_example_controller.py tests/integration/test_reference_resources.py tests/config/test_routes.py -q`
- 계정·토큰·현재 사용자: `uv run pytest --no-cov tests/auth tests/test_auth_controller.py tests/test_user_controller.py -q`
- 공개 route·OpenAPI, 동일 이메일 동시 등록, login 중 비활성화, refresh 경쟁·재사용 폐기, 오류 응답 전에 폐기 commit을 함께 검증한다. API 변경의 최종 게이트는 루트 지침을 따른다.

### Common Patterns

`ExamplesController`는 선언만 가진 얇은 CRUD controller다. 특수 인증 액션은 JsonApiController의 router에 route를 명시 등록하고 공용 문서 모델·오류 응답 선언을 사용한다.

## Dependencies

- 내부: [concerns](../../concerns/AGENTS.md), [auth](../../../auth/AGENTS.md), [models](../../../models/AGENTS.md), [schemas](../../../schemas/AGENTS.md), [serializers](../../../serializers/AGENTS.md), `config/routes.py`·`config/database.py`·`config/auth.py`.
- 외부: FastAPI/Starlette, SQLAlchemy 2와 PostgreSQL.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->

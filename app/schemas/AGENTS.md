<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# 쓰기 Schema와 조회 정책 지침

## Purpose

Pydantic으로 JSON:API 쓰기 입력을 검증하고 자원별 QueryPolicy에 조회 허용 범위를 선언한다. ORM 저장 구조나 공개 응답 조립을 소유하지 않는다.

## Key Files

| 파일 | 역할 |
| --- | --- |
| `__init__.py` | Example write·관계 schema, QueryPolicy, 인증 문서·normalize_email export |
| `example.py` | strict create/update/replace·관계 입력과 Example 조회 allowlist·인덱스 판단 |
| `example_category.py` | name/createdAt 정렬·name 필터의 참조 분류 QueryPolicy |
| `example_tag.py` | 참조 라벨 QueryPolicy와 인덱스 생략 근거 |
| `auth.py` | register/login/refresh·logout strict 문서와 이메일 정규화 |

## For AI Agents

### Working In This Directory

- write schema는 `app/jsonapi/naming.py`의 `JsonApiWriteSchema`를 상속해 camelCase·extra 금지·strict 설정을 함께 받는다. 자체 ConfigDict·StrictStr·변환 함수를 다시 선언하지 않는다. 내부 이름으로의 모델 변환은 controller가 수행하고 출력 필드는 serializer에 선언한다.
- ExampleCreate·ExampleReplace는 title/status/score가 필수이고 description은 nullable이다. ExampleUpdate는 MISSING으로 생략을 구분한다. PATCH를 PUT과 같은 필수 입력으로 바꾸거나 생략을 null로 덮어쓰지 않는다.
- title은 strict 문자열 1–200자, score는 strict 정수 0–100이며 status는 ExampleStatus다. 저장 enum·DB check·API 입력 범위를 함께 검토한다.
- ExampleStatus는 각 schema에 `Annotated[ExampleStatus, Field(strict=False)]`로 선언한다. JSON 문자열 enum을 받기 위한 한정된 strict 예외이며, PEP 695 별칭으로 감싸 OpenAPI의 ExampleStatus component 이름을 바꾸지 않는다.
- 관계 입력은 ResourceIdentifier linkage다. category는 to-one nullable, tags는 to-many 목록이고 ExampleRelationships 문서가 있으면 적어도 한 관계가 필요하다. 내부 category_id나 임의 관계를 직접 입력으로 열지 않는다.
- 인증 문서는 strict type와 extra 금지를 유지한다. register type은 `users`, login은 `authCredentials`, refresh/logout은 `refreshTokens`이고 token은 body의 `refreshToken` 속성이다.
- 이메일은 EmailStr·최대 254자, 비밀번호는 12–128자, refresh token은 비어 있지 않은 문자열이다. `normalize_email()`은 strip·casefold를 하며 controller가 저장·검색 직전에 명시 호출한다.

### 조회 Allowlist

| 항목 | 현재 허용 범위 |
| --- | --- |
| filter title | exact, contains |
| filter status | exact, in |
| filter score | exact, gt, gte, lt, lte, in |
| filter category.id | exact, in, isNull |
| filter createdAt | exact, gt, gte, lt, lte; UTC offset 있는 datetime |
| sort | title, status, score, createdAt, updatedAt |
| include | category, tags |
| 기본 순서 | createdAt 내림차순, id tie breaker |

- QueryPolicy에 실제 SQLAlchemy 열과 값 parser를 선언한다. 사용자 입력에서 임의 SQL 열·관계 경로·expression을 구성하지 않는다.
- include는 serializer 관계 선언과 policy 양쪽에 있어야 한다. 새로운 query parameter나 `fields[...]` 희소 필드셋을 추가하지 않는다.

- category/tag 정책은 name의 exact·contains 필터, name·createdAt 정렬, name ASC·id ASC 기본 순서와 빈 includes를 가진다. 서버 관리 자원이라 write schema가 없다.
- 인덱스 판단은 각 정책 선언부에 남긴다. Example 기본 정렬·category.id는 인덱스로 지원하며 다른 시범 필터·정렬은 접근 패턴 근거가 없어 생략한다. contains의 `%...%`에는 btree가 맞지 않는다.
- 참조 테이블은 name 고유값의 동점 그룹이 1이고 규모가 작아 `(name, id)` 인덱스를 생략한다. createdAt은 동점이 가능하므로 작은 규모만이 생략 근거다. Example의 Sort 없는 계획 테스트를 참조 자원에 복사하지 않는다.

### Testing Requirements

`TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리킬 때 `uv run pytest --no-cov tests/schemas tests/controllers tests/jsonapi/test_query.py tests/test_auth_controller.py tests/integration/test_reference_resources.py -q`를 실행한다. URL이 없으면 `./scripts/check.sh`의 임시 Docker PostgreSQL 게이트를 사용한다. 입력 필수성·strict 값·camelCase·오류 pointer와 실제 SQL 조회 allowlist를 함께 확인한다.

### Common Patterns

쓰기는 JsonApiWriteSchema, 생략은 MISSING, 관계는 ResourceIdentifier, filter는 parser를 가진 FilterField로 표현한다. 새 public schema는 `__init__.py`에서 export하고 controller 선언에 연결한다.

## Dependencies

- 내부: [models](../models/AGENTS.md)의 Example·ExampleStatus, [JSON:API](../jsonapi/AGENTS.md)의 ResourceIdentifier·QueryPolicy·FilterField·SortTerm, [controller](../controllers/AGENTS.md).
- 외부: Pydantic·EmailStr·MISSING, Python datetime·UUID.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->

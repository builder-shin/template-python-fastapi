# API 애플리케이션 계층 지침

이 문서는 `app/` 아래의 모델, schema, serializer, controller와 하위 프로토콜 모듈의 책임 경계를 정한다. `controllers/concerns`, `jsonapi`, `serializers`의 세부 규칙은 각각의 하위 `AGENTS.md`를 따른다. 테스트의 세부 규칙은 `tests/AGENTS.md` 아래에 둔다.

## 자원 표현의 소유권

| 위치 | 소유하는 것 | 소유하지 않는 것 |
| --- | --- | --- |
| `models/` | SQLAlchemy 2 테이블, 제약조건, FK, ORM 관계, 저장 enum | HTTP 입력 검증, 공개 필드 선택 |
| `schemas/` | Pydantic 쓰기 입력, camelCase alias, 관계 linkage 입력, `QueryPolicy` allowlist | ORM 저장과 JSON 응답 조립 |
| `serializers/` | JSON:API `type`, attributes, relationships, include serializer, eager-load 선언 | 요청 값 검증과 SQL filter 해석 |
| `controllers/api/v1/` | 자원별 model·schema·serializer·조회 정책의 선언과 필요한 도메인 hook | 별도 CRUD 구현 또는 라우트 자동 등록 |

## 모델과 migration

- 새 ORM 모델은 `app/models/__init__.py`에서 import되어 Alembic metadata에 포함되게 한다. 관계의 cascade, nullability, FK 삭제 정책과 DB 제약조건은 도메인 요구를 모델과 migration에 함께 반영한다.
- 새 자원의 public symbol은 `app/models/__init__.py`, `app/schemas/__init__.py`, `app/serializers/__init__.py`, `app/controllers/api/v1/__init__.py`에 필요한 범위로 함께 export한다. route 조립은 마지막 controller export를 `config/routes.py`에서 import하는 구조이므로, 내부 파일만 추가해 공개 조립점이 끊기게 하지 않는다.
- PostgreSQL 전용 타입·기능을 숨기지 말고 실제 migration으로 검증한다. schema 변경을 런타임 `create_all`이나 테스트 전용 테이블 생성으로 전달하지 않는다.
- `TimestampMixin` 등 이미 제공되는 공통 모델 기반을 우선 사용한다. 단일 자원을 위해 base class, repository 또는 service 계층을 추가하지 않는다.

## schema와 조회 정책

- create, update, replace schema는 서로의 필수성 의미를 보존한다. `PATCH`는 `MISSING` 기반의 부분 갱신이고, `PUT` replace/upsert는 완전한 attributes를 받는다.
- 쓰기 schema는 `extra="forbid"`와 camelCase alias를 유지한다. 관계 입력은 `ResourceIdentifier` linkage만 사용하며 내부 FK를 직접 공개 입력으로 만들지 않는다.
- `QueryPolicy`에는 실제로 지원할 filter 연산자, sort 열, include 경로, 기본 정렬과 결정적 tie breaker를 명시한다. 새로운 query parameter나 `fields[...]`는 추가하지 않는다.
- filter parser는 저장 형식에 맞는 값을 엄격하게 변환한다. SQLAlchemy expression이나 사용자 입력 열 이름을 동적으로 조합하지 않는다.

## serializer와 controller

- serializer는 선언한 attributes와 `RelationshipDefinition`만 직렬화한다. 공개 관계에는 대상 serializer와 cardinality를 명시해 linkage와 compound `included` 문서를 일관되게 만든다.
- serializer의 `resource_path`는 `config/routes.py`에서 controller를 조립할 때 쓰는 prefix와 정확히 같아야 한다. 이 값이 resource·relationship link와 POST·PUT의 `Location` 기준이므로, 한쪽만 바꾸지 않는다.
- include 경로는 serializer 선언과 `QueryPolicy.includes` 양쪽에서 허용되어야 한다. serializer의 loader option을 우회하는 지연 로딩을 route 안에 추가하지 않는다.
- controller는 `CrudActions`를 상속하고 `model_class`, `serializer_class`, 세 write schema, `relationships_schema`, `query_policy`를 선언한다. `enable_upsert = True`는 PostgreSQL PUT 계약이 필요한 자원에만 둔다.
- 자원별 규칙이 공통 lifecycle hook으로 표현되지 않을 때만 controller 메서드를 재정의한다. 예외는 `JsonApiException`으로 전달하고 일반 JSON 응답을 직접 만들지 않는다.

## 새 자원 점검

1. 모델·관계와 Alembic migration을 추가하고 `app/models/__init__.py` export를 갱신한다. 새 head revision이면 PostgreSQL migration 테스트의 head 기대값을, table을 추가·삭제하면 빈 database table-set 기대값을 함께 갱신한다.
2. 쓰기 schema, 관계 linkage schema, `QueryPolicy`, serializer를 추가해 공개 입력과 출력 범위를 고정하고 `schemas`·`serializers` 공개 export를 갱신한다.
3. `resource_path`와 같은 prefix의 얇은 controller를 선언하고 `controllers/api/v1` export와 `config/routes.py`의 명시 route 등록을 함께 추가한다.
4. `create_app()` 기반 `tests/test_<resource>_controller.py`에서 실제 route 조립, OpenAPI, 대표 성공·거부 응답을 확인하고, 필요한 protocol·관계·upsert 회귀는 실제 PostgreSQL fixture로 추가한다.
새 자원 변경 후에는 `uv run pytest --no-cov tests/models tests/serializers tests/controllers tests/test_*_controller.py -q`로 공통·공개 route 회귀를 함께 확인한다. migration이 포함되면 `uv run pytest --no-cov tests/config tests/integration -q`도 실행하고, 병합 전에는 `./scripts/check.sh`를 실행한다.

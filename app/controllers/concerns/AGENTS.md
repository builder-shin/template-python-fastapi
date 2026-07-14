# 공통 CRUD concern 지침

이 디렉터리는 자원별 controller가 공유하는 JSON:API 라우트·입력 문서·트랜잭션 동작만 둔다. 제품별 정책이나 특정 자원 이름을 이곳에 넣지 않는다.

## `CrudActions`의 선언 계약

- 상속 controller는 `model_class`, `serializer_class`, `create_schema`, `update_schema`, `replace_schema`, `query_policy`를 선언한다. 쓰기 가능한 관계가 있으면 `relationships_schema`를 선언한다.
- `__init__`은 선언에서 collection/resource/relationship route를 만든다. 라우트 decorator, 자동 탐색, `config/routes.py` 외의 숨은 등록을 추가하지 않는다.
- resource URL은 index/create와 show/update/destroy를 제공하고, `enable_upsert`일 때만 `PUT /{resource_id}`를 등록한다. relationship linkage와 related-resource URL은 serializer에 선언된 관계에서 파생된다.
- 읽기 scope 확장은 `index_scope`, 쓰기 규칙 확장은 `model_params`, `assign_relationships` 또는 문서화된 lifecycle hook을 사용한다. 공통 메서드 전체를 복사하지 않는다.

## 입력·관계·응답

- `JsonApiRoute`는 FastAPI가 body를 읽기 전에 쓰기 요청의 `Content-Type`을 검증한다. `write_document_model`과 `relationship_document_model`의 strict camelCase 문서 구조를 느슨하게 만들지 않는다.
- controller route는 JSON:API `Accept` 의존성과 `JsonApiResponse`를 유지한다. 오류는 `JsonApiException`으로 source pointer/parameter/header를 남겨 전역 handler에 맡긴다.
- 관계 입력은 serializer가 선언한 public relationship만 허용한다. 대상 type, ID 변환, 중복 linkage, 존재 여부를 검증한 뒤 부모 자원의 같은 transaction 안에서 갱신한다.
- to-many linkage는 `POST` add, `PATCH` replace, `DELETE` remove를 사용하고, to-one은 `PATCH` replace만 사용한다. relationship mutation의 성공 응답은 204다.

## 트랜잭션과 hook

- create, update, destroy, relationship mutation은 `session.begin()` 경계 안에서 flush 후 응답 문서를 만든다. hook에서 예외가 나면 전체 변경이 rollback되어야 한다.
- `before_create`/`after_create`, `before_update`/`after_update`, `before_upsert`/`after_upsert`, `before_destroy`/`after_destroy`는 해당 transaction 안에서만 실행한다. 외부 I/O나 별도 commit을 넣지 않는다.
- `PATCH`는 주어진 attributes와 relationships만 바꾸며, `PUT`은 생성을 하거나 완전 교체한다. 기존 `PUT`에서 생략한 쓰기 가능 관계는 기본값으로 재설정되는 계약을 유지한다.
- `PUT`은 PostgreSQL advisory transaction lock 후 `postgresql_insert(...).on_conflict_do_update(...)`를 사용한다. 동일 ID 동시 요청, hook 실패, 직렬화 실패의 rollback 동작을 약화하지 않는다.

## 변경 확인

- CRUD 동작: `uv run pytest --no-cov tests/controllers/test_crud_actions.py -q`
- 관계 동작: `uv run pytest --no-cov tests/controllers/test_relationship_actions.py -q`
- PostgreSQL PUT 경쟁과 rollback: `uv run pytest --no-cov tests/controllers/test_upsert.py -q`

공통 동작 변경은 선언형 controller와 위 세 테스트 묶음을 함께 검토한다. SQLite mock이나 직접 호출만으로 transaction 계약을 판정하지 않는다.

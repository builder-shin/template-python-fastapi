# 공통 CRUD concern 지침

이 디렉터리는 모든 controller가 공유하는 라우터 조립 규칙과 자원별 controller가 공유하는 JSON:API 라우트·입력 문서·트랜잭션 동작만 둔다. 제품별 정책이나 특정 자원 이름을 이곳에 넣지 않는다.

## `JsonApiController`의 조립 계약

- 모든 컨트롤러는 이 base를 상속하고 `self.router`에만 route를 등록한다. `APIRouter(...)`를 컨트롤러에서 다시 조립하지 않는다.
- base가 prefix 검증(`validate_route_prefix`), JSON:API `Accept` 의존성(`require_jsonapi_accept`), 쓰기 `Content-Type` 검증(`JsonApiRoute`), `self.prefix` 노출을 소유한다. 이 넷은 router 키워드 인자라 빠뜨려도 mypy·ruff가 잡지 못하므로 컨트롤러에 복사하지 않는다.
- `Accept` 협상을 의도적으로 생략하는 컨트롤러만 `negotiate_accept = False`를 선언한다. 이때도 `JsonApiRoute`의 쓰기 media type 검증은 유지된다.
- 절대 경로를 등록하는 컨트롤러만 `allow_root_prefix = True`를 선언하고 prefix를 넘기지 않는다. 이 선언이 없으면 빈 prefix도 `validate_route_prefix`가 거부하므로, prefix를 빠뜨린 컨트롤러가 조용히 루트에 마운트되지 않는다.
- 오류 응답 선언은 `app/jsonapi/responses.py`의 `jsonapi_error_responses()`를 그대로 쓴다. description 문구는 컨트롤러가 아니라 `ERROR_RESPONSE_DESCRIPTIONS` 한 곳에서만 바꾼다.
- route `name=`은 operationId의 근거이므로 base가 자동 생성하지 않는다. 컨트롤러가 `"<Controller>.<action>"` 형식으로 명시한다.

## 모듈 경계

`CrudActions`는 이 디렉터리의 유일한 공개 진입점이고, 내부는 책임별 모듈로 나뉜다. 상속 체인은 `CrudDeclarations` → `CrudRelationships` → `CrudUpsert` → `CrudActions` 순이며 **모든 참조는 아래 방향으로만** 향한다. 상위 계층이 하위에 없는 멤버를 부르기 시작하면 Protocol/stub 보일러플레이트가 생기므로 배치를 옮기지 않는다.

| 모듈 | 소유하는 책임 | 이곳에서 검토할 변경 |
| --- | --- | --- |
| `crud_actions.py` | `CrudActions` 조립(`__init__`), `index`·`show`·`create`·`update`·`destroy` | 컬렉션/단일 자원 액션, 문서 스키마 생성 순서, route 등록 호출 순서 |
| `crud_base.py` | `CrudDeclarations`: 선언 계약, `index_scope`, `model_params`, 8개 lifecycle 훅, 단일 자원 조회 | 확장 지점 시그니처, `_find_resource*`, PK 변환 |
| `relationship_resolver.py` | `CrudRelationships`: `show_relationship`·`show_related`·`mutate_relationship`, `assign_relationships`, linkage 해석 | 관계 액션, linkage 검증/할당, 대상 모델 해석 |
| `upsert_executor.py` | `CrudUpsert`: `PUT` 액션과 SQLAlchemy 상태 조작 | advisory lock, `ON CONFLICT DO UPDATE`, 생성 경로 관계 재적용 |
| `route_registrar.py` | route 등록, OpenAPI `responses`, FastAPI delegate 생성 | 라우트 이름/operationId, status code 집합, request body 주입 |
| `document_parsing.py` | 무상태 JSON:API 요청 문서 파싱·검증 | 오류 code·source pointer 문자열 |
| `jsonapi_controller.py` / `jsonapi_routes.py` | 라우터 조립과 write 문서 모델 | prefix 검증, `Accept`/`Content-Type`, 문서 모델 생성 |

- `route_registrar.py`의 두 등록 함수 호출 순서와 그 안의 `add_api_route` 순서가 OpenAPI `paths`·operation 순서를 결정한다. 재배치는 문서 변경이므로 하지 않는다.
- delegate closure에는 docstring을 달지 않는다. FastAPI가 operation `description`으로 발행한다.

## `CrudActions`의 선언 계약

- 상속 controller는 `model_class`, `serializer_class`, `create_schema`, `update_schema`, `replace_schema`, `query_policy`를 선언한다. 쓰기 가능한 관계가 있으면 `relationships_schema`를 선언한다.
- router prefix 검증과 오류 응답 선언은 위 `JsonApiController` 계약을 따른다. controller마다 같은 검사나 description 문자열을 다시 만들지 않는다.
- `__init__`은 선언에서 collection/resource/relationship route를 만든다. 라우트 decorator, 자동 탐색, `config/routes.py` 외의 숨은 등록을 추가하지 않는다.
- resource URL은 index/create와 show/update/destroy를 제공하고, `enable_upsert`일 때만 `PUT /{resource_id}`를 등록한다. relationship linkage와 related-resource URL은 serializer에 선언된 관계에서 파생된다.
- related-resource URL(`GET /{resource_id}/{관계}`)은 대상 serializer로 응답을 만들므로 대상 테이블을 직접 조회하며 `대상 serializer.loader_options(...)`(= 그 serializer의 `required_loader_paths()`)를 건다. 소유 serializer의 loader만 걸면 대상 serializer가 자기 관계를 읽는 순간 미적재 오류로 500이 된다.
- `index`는 기본적으로 COUNT를 실행하지 않는다. `page[totals]=true`일 때만 COUNT 한 번을 추가해 `meta.totalCount`와 `links.last`를 채우고, 그 외에는 `apply_pagination(..., probe=True)`가 읽은 한 행으로 `next` 존재만 판정한다. `page[after]`/`page[before]`가 있으면 `apply_keyset` + `keyset_sorts`로 OFFSET 없이 페이지를 자르고, `page[before]`는 역순으로 읽은 뒤 Python에서 되뒤집는다. COUNT와 keyset WHERE는 모두 `index_scope` → `apply_filters` 결과 위에 쌓아 확장 지점을 유지한다.
- to-many related URL은 collection 정책을 따른다. `page[number]`/`page[size]`만 받고(기본 20, 최대 100), 대상 기본 키 오름차순으로 정렬하며 `meta.totalCount`와 pagination `links`를 함께 반환한다. `filter`·`sort`·`include`는 지원하지 않는다. to-one related URL은 페이지 개념이 없으므로 모든 query parameter를 거부한다. 이 완화는 `show_related`의 to-many 분기에서만 하고 공용 `document_parsing.reject_query_parameters`는 그대로 둔다.
- 읽기 scope 확장은 `index_scope`, 쓰기 규칙 확장은 `model_params`, `assign_relationships` 또는 문서화된 lifecycle hook을 사용한다. 공통 메서드 전체를 복사하지 않는다.
- `index_scope`는 행을 늘리지 않아야 한다. to-many join이 필요하면 `relationship.any(...)`를 쓰거나 `.distinct()`로 접는다. `LIMIT size + 1` probe는 DB가 조인 결과 행에 적용하고 중복 제거는 그 뒤에 일어나므로, 행이 늘어나면 한 페이지가 `page[size]`보다 짧아진다. `index`는 이 때문에 probe를 중복 제거 이전 행 수로 판정한다.

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
- `PUT` 생성 경로는 자원을 다시 읽지 않는다. 컬럼 상태는 그 statement의 `RETURNING` 행에서 채우고, serializer가 선언한 모든 관계를 응답 문서 생성 전에 명시적으로 적재한다. serializer는 미적재 공개 관계를 lazy-load하지 않고 실패시키므로 관계 하나라도 빠뜨리면 500이 된다.
- 생성 여부는 사전 `SELECT`가 아니라 `RETURNING`에 실은 `xmax = 0` 판별식으로 확정한다. 사전 검사와 실제 branch가 어긋나면(동시 트랜잭션이 그 사이에 행을 commit한 경우) 자원을 다시 읽어 요청된 관계 값만 다시 적용한다. 이때 in-place 경로는 쓰지 않는다. 그 경로의 "방금 insert되었다"는 전제가 더 이상 참이 아니기 때문이다.
- 생성된 to-one은 relationship의 `local_remote_pairs`로 조회한다. local 컬럼을 대상 primary key로 가는 외래 키로 가정하지 않는다. 외래 키가 상대편에 있는 관계에서는 그 가정이 남의 행을 linkage로 실어 보낸다.
- upsert 후보를 영속 역방향 collection에서 떼어낼 때는 그 역방향 속성이 이미 적재되었거나 backref 변경이 대기 중일 때만 읽는다. 무조건 읽으면 advisory lock 구간 안에서 대상의 역방향 collection 전체를 적재한다.

## 변경 확인

- CRUD 동작: `uv run pytest --no-cov tests/controllers/test_crud_actions.py -q`
- 관계 동작: `uv run pytest --no-cov tests/controllers/test_relationship_actions.py -q`
- PostgreSQL PUT 경쟁과 rollback: `uv run pytest --no-cov tests/controllers/test_upsert.py -q`
- 라우터 조립 계약: `uv run pytest --no-cov tests/controllers/test_jsonapi_controller.py -q`

공통 동작 변경은 선언형 controller와 위 네 테스트 묶음을 함께 검토한다. SQLite mock이나 직접 호출만으로 transaction 계약을 판정하지 않는다.

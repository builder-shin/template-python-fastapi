<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# 공통 Controller·CRUD Concern 지침

## Purpose

모든 controller의 router 조립 계약과 자원별 JSON:API 조회·입력·관계·transaction을 담당한다. 자원 controller의 공개 CRUD 진입점은 CrudActions 하나이며, 인증·사용자·health는 JsonApiController를 직접 사용한다. 제품별 정책이나 특정 자원 이름을 공통 concern에 넣지 않는다.

## Key Files

| 파일 | 역할 |
| --- | --- |
| `__init__.py` | CrudActions·JsonApiController public export |
| `jsonapi_controller.py` | prefix·Accept·JsonApiRoute·self.prefix를 소유하는 공통 base |
| `jsonapi_routes.py` | prefix 검사, body 파싱 전 Content-Type 검증, write·relationship 문서 builder |
| `crud_base.py` | CrudDeclarations: 선언 계약·scope·params·8개 lifecycle hook·단일 자원 조회 |
| `document_parsing.py` | 무상태 문서·type·ID·query 검증과 ID 변환 |
| `relationship_resolver.py` | CrudRelationships: linkage 검증·할당·관계 액션과 related 조회 |
| `upsert_executor.py` | CrudUpsert: PostgreSQL PUT·advisory lock·RETURNING·ORM 상태 복원 |
| `route_registrar.py` | resource·relationship route 등록, 공용 OpenAPI 오류 선언 사용, endpoint delegate |
| `crud_actions.py` | CrudActions 조립, index/show/create/update/destroy |

## For AI Agents

### 조립과 모듈 경계

- 모든 controller는 JsonApiController를 상속하고 받은 self.router에 route를 등록한다. controller에서 APIRouter를 다시 조립하지 않는다.
- base가 validate_route_prefix, require_jsonapi_accept, JsonApiRoute와 self.prefix를 소유한다. Accept 생략은 `negotiate_accept=False`, 루트 마운트는 `allow_root_prefix=True`로 명시한다. Accept를 생략해도 body Content-Type 검증은 유지한다.
- 오류 응답은 `app/jsonapi/responses.py`의 `jsonapi_error_responses()`를 사용하고 description은 ERROR_RESPONSE_DESCRIPTIONS에서만 바꾼다. route name은 operationId의 근거이므로 `<Controller>.<action>` 형태로 명시한다.
- 상속은 `CrudDeclarations → CrudRelationships → CrudUpsert → CrudActions` 순이다. 내부 의존성은 기반 계층으로만 향하고 기반 클래스가 파생 클래스에만 있는 멤버를 요구하지 않는다.
- route_registrar의 등록 함수 호출 순서와 add_api_route 순서는 OpenAPI paths·operation 순서를 결정하므로 재배치하지 않는다. delegate closure에 docstring을 달면 operation description으로 노출되므로 추가하지 않는다.
- controller 인스턴스의 앱 등록은 config/routes.py에서 명시적으로 한다. 자동 탐색·decorator·숨은 등록과 공통 액션 복사를 추가하지 않는다.

### 선언·조회·관계

- 쓰기 자원은 model_class·serializer_class·세 write schema·query_policy를 선언하고 쓰기 관계가 있으면 relationships_schema도 선언한다. `enable_upsert=True`일 때만 PUT을 등록한다.
- `enable_writes=False`이면 create/update/upsert/destroy와 관계 mutation route를 등록하지 않는다. 세 write schema를 생략하고 제네릭의 해당 위치에는 BaseModel을 쓴다. writable 관계 이름은 빈 집합으로 유지한다.
- read_dependencies·write_dependencies는 resource와 relationship 경로 모두에 적용한다. write dependency가 있으면 401·403 오류도 OpenAPI에 포함한다.
- collection index는 index_scope → apply_filters 위에 정렬·페이지 조건을 쌓는다. 기본은 COUNT 없이 size+1행 probe로 next를 판단하고, `page[totals]=true`일 때만 COUNT·meta.totalCount를 추가한다. offset 모드의 last도 이 총계로 채운다.
- `page[after]`·`page[before]`는 apply_keyset·keyset_sorts로 OFFSET 없이 조회한다. before는 DB 정렬을 반전하고 결과를 다시 뒤집는다. COUNT는 cursor WHERE를 넣기 전 scope·filter에 적용한다.
- index_scope는 행을 늘리지 않는다. to-many 조건은 relationship.any(...) 또는 distinct로 접는다. LIMIT은 DB의 원래 행에 적용되므로 probe를 identity 중복 제거 전에 판정하되, scope가 행을 늘려 페이지 길이가 줄어드는 구조를 만들지 않는다.
- index/show는 `loader_options(..., linkage_only=True)`를 사용한다. 쓰기·relationship·related는 관계 객체가 필요하므로 기본 전체 eager loader를 사용한다.
- related URL은 대상 테이블을 직접 조회하고 `대상 serializer.loader_options(...)`를 적용한다. 소유 serializer의 loader만 걸어 대상의 공개 관계가 미적재된 채 직렬화되지 않게 한다.
- to-many related URL은 page[number]·page[size]만 받아 기본 20·최대 100개를 대상 PK 오름차순으로 반환하며 COUNT·meta.totalCount·pagination links를 포함한다. filter·sort·include·totals·cursor는 허용하지 않는다.
- resource GET은 include만 받는다. to-one related·linkage GET·쓰기는 모든 query parameter를 거부한다. to-many related의 제한된 완화는 show_related 분기에만 두고 공용 `document_parsing.reject_query_parameters`는 그대로 둔다.
- 관계는 public 이름·대상 type·ID 변환·중복 linkage·존재 여부를 검증하고 부모와 같은 transaction에서 할당한다. to-many는 POST add/PATCH replace/DELETE remove, to-one은 PATCH replace만 제공하며 mutation은 빈 204다.

### 입력·Transaction·Hook

- 문서 builder는 naming.py의 WRITE_MODEL_CONFIG로 strict camelCase·extra 금지·필수 ID/attributes 의미를 공유한다. POST client ID는 403, type·path/body ID 불일치는 409다. PATCH는 attributes 또는 relationships가 필요하고 빈 attributes는 허용한다.
- index_scope·model_params·assign_relationships·before_*/after_*를 확장점으로 사용한다. 공통 메서드 전체를 자원 controller에 복사하지 않는다.
- 요청 session 연결은 route delegate의 get_request_session이 담당해 전역 IntegrityError rollback을 보존한다. 인증 조회는 그 전에 짧은 별도 session을 닫으므로 write session의 begin과 충돌하지 않는다.
- create/update/upsert는 session.begin() 안에서 flush·hook·응답 직렬화를 마친다. destroy·관계 mutation도 명시 transaction을 사용한다. hook·직렬화 실패는 전체 변경을 rollback해야 하며 hook에 외부 I/O나 별도 commit을 넣지 않는다.
- PATCH는 제공된 값만 바꾼다. 관계가 포함된 PATCH와 관계 mutation은 부모 행을 잠근다. PUT은 완전한 attributes를 받고 기존 자원에서 생략한 쓰기 가능 관계를 None/[]로 초기화하되 write schema 밖 관계는 보존한다.

### PostgreSQL PUT

- 테이블·ID에서 유도한 advisory transaction lock 후 `INSERT ... ON CONFLICT DO UPDATE`를 실행한다. 단순 사전 조회나 SQLite 경로로 대체하지 않는다.
- mapped column 변경·updated_at·관계 재적용·hook과 일반 생성 201/교체 200·생성 Location 계약을 함께 검토한다. upsert 본문을 나눠 lock·no_autoflush·SQL 경계를 이동하지 않는다.
- 실제 INSERT인 생성 경로는 두 번째 resource SELECT 없이 RETURNING column으로 후보를 영속화한다. 직렬화 전에 모든 선언 관계를 명시적으로 적재·초기화한다.
- RETURNING의 `xmax = 0` 판별식으로 실제 SQL 분기를 확인한다. 사전 생성 검사와 달리 SQL이 기존 행을 갱신했다면 자원을 다시 조회하고 관계 값을 재적용하며, 방금 insert된 행이라는 전제의 in-place 경로를 사용하지 않는다.
- 생성된 to-one은 관계 local_remote_pairs로 조회한다. 로컬 column이 항상 대상 PK를 가리키는 FK라고 가정하지 않는다.
- transient 후보의 역방향 collection은 이미 적재되었거나 pending backref 변경이 있을 때만 읽는다. advisory lock 구간에서 불필요한 역관계 전체 조회를 일으키지 않는다.

### Testing Requirements

독립된 `*_test` PostgreSQL의 TEST_DATABASE_URL이 있을 때만 아래 좁은 pytest를 실행한다. 없으면 `./scripts/check.sh`가 만드는 임시 Docker PostgreSQL 게이트를 사용한다.

| 변경 | 확인 |
| --- | --- |
| CRUD·query·hook | `uv run pytest --no-cov tests/controllers/test_crud_actions.py -q` |
| 관계·부모 행 잠금·related 페이지 | `uv run pytest --no-cov tests/controllers/test_relationship_actions.py -q` |
| PUT 경쟁·RETURNING·rollback | `uv run pytest --no-cov tests/controllers/test_upsert.py -q` |
| router base·협상·prefix | `uv run pytest --no-cov tests/controllers/test_jsonapi_controller.py -q` |

공통 변경은 네 묶음과 선언형 controller를 함께 검토한다. SQLite mock이나 직접 호출만으로 DB 계약을 판정하지 않는다.

### Common Patterns

구체적인 write 문서 타입을 delegate annotation에 주입해 OpenAPI를 생성한다. 순수 문서 검증은 document_parsing, 선언·hook은 crud_base, SQL 관계는 relationship_resolver, PUT 상태 복원은 upsert_executor에서 검토한다.

## Dependencies

- 내부: [JSON:API](../../jsonapi/AGENTS.md) 문서·naming·query·오류, [serializer](../../serializers/AGENTS.md) loader, config/database.py의 get_request_session.
- 외부: FastAPI/Starlette, Pydantic MISSING·create_model, SQLAlchemy 2와 PostgreSQL.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->

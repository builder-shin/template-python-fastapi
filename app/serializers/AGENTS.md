<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# 공개 Serializer 지침

## Purpose

선언한 attributes·relationships만 JSON:API 자원으로 직렬화하고, linkage·canonical link·compound included 문서와 필요한 eager loader를 함께 정의한다. 입력 검증이나 SQL filter 해석은 이 계층의 책임이 아니다.

## Key Files

| 파일 | 역할 |
| --- | --- |
| `__init__.py` | serializer·기반 타입·include helper public export |
| `base.py` | JsonApiSerializer, RelationshipDefinition, include 순회·loader·안전한 JSON 값 검증 |
| `example_serializer.py` | examples 공개 attributes와 category·tags 관계, canonical resource 경로 |
| `example_category_serializer.py` | exampleCategories type·name, /api/v1/categories/{id} canonical 링크 |
| `example_tag_serializer.py` | exampleTags type·name, /api/v1/tags/{id} canonical 링크 |
| `user_serializer.py` | users 공개 profile과 고정 /api/v1/users/me self URL |
| `auth_token_serializer.py` | authTokens 토큰 쌍·Bearer·만료 메타데이터, resource link 없음 |

## For AI Agents

### 선언과 공개 표현

- JsonApiSerializer에 type_name, 공개 attributes와 필요한 relationships를 명시한다. attributes는 내부 snake_case 이름만 선언하고 공개 이름은 naming.py의 snake_to_camel로 변환한다. 별도 변환 함수를 만들지 않는다. 선언하지 않은 모델 필드를 읽거나 내보내지 않는다.
- RelationshipDefinition은 public 이름에 내부 attribute·대상 serializer·many cardinality를 고정한다. 선언은 실제 ORM 관계여야 하고 many와 ORM cardinality가 일치해야 한다.
- 선택적인 linkage_attribute는 to-one의 로컬 FK column 속성이며 대상의 단일 PK를 가리켜야 한다. secondary가 없고 primaryjoin이 정확히 그 FK 등식 하나여야 한다. 추가 join 조건·역방향 FK·복합키를 허용해 include 유무에 따라 linkage가 달라지게 하지 않는다.
- to-many 값은 문자열이 아닌 순차 컬렉션, to-one은 하나의 모델 또는 None이다. 안전한 linkage shape를 임의 값으로 확장하지 않는다.
- resource_path는 기본 self URL과 관계 self·related URL의 기준이며 None이면 해당 resource link를 만들지 않는다. custom canonical URL은 resource_location()을 재정의한다. CRUD prefix·Location과 함께 검토한다.
- UserSerializer는 email·isActive·createdAt·updatedAt만 반환한다. password_hash·refresh_sessions는 공개하지 않는다. AuthTokenSerializer는 발급 결과 dataclass의 토큰 쌍만 표현하며 저장 RefreshSession 모델을 직접 직렬화하지 않는다.
- 새 공개 serializer와 기반 타입은 `__init__.py`에서 export한다. `fields[...]`를 지원하거나 schema를 응답 필드 선택의 근거로 사용하지 않는다.

### 로더와 관계 값

- 조회문은 required_loader_paths()의 선언 경로에 loader_options()를 적용한다. 기본값은 전체 column eager-load이며 쓰기·relationship·related 경로는 실제 관계 객체가 필요하므로 기본값을 유지한다.
- 순수 index/show만 loader_options(..., linkage_only=True)를 사용한다. include로 요청한 관계는 전체 적재하고, 미요청 to-one의 선언된 linkage_attribute가 있으면 로컬 FK로 linkage를 만들어 대상 조회를 생략한다. 미요청 관계를 조회해야 하면 대상 PK만 load_only(..., raiseload=True)로 읽는다.
- Example.category는 linkage_attribute="category_id"를 선언한다. to-many PK 전용 적재 객체의 다른 column은 raiseload로 실패하므로 조회 hook에서 관련 속성이 필요하면 include 또는 전체 eager loader를 사용한다.
- loader_paths()는 경로와 include 요청 여부를 제공한다. 경로·loader option은 serializer·모델·include·linkage_only 조합에 따라 최대 512개 LRU cache를 사용하므로 선언을 요청마다 바꾸지 않는다.
- persistent·detached의 미적재 관계는 lazy query 없이 JsonApiSerializationError로 실패한다. 단, 적재된 linkage_attribute가 있는 to-one은 그 FK로 linkage를 만들 수 있다. FK까지 미적재라면 같은 오류로 실패한다.
- 중첩 include는 선언 관계를 따라 추가 loader 경로를 만든다. route에서 별도 지연 로딩으로 우회하지 않는다.
- initialize_relationship_defaults()는 transient·pending의 미적재 관계에만 None/[]를 설정한다. persistent·detached에 기본값을 주입하지 않는다.

### Include 순회

- include의 모든 segment는 비어 있지 않고 serializer에 선언되어야 한다. 트리는 중복 경로를 합치며 임의 숫자 깊이 제한을 두지 않는다.
- included에 primary resource를 다시 넣지 않는다. 같은 (type, id) 보조 자원은 처음 발견한 순서대로 한 번만 넣는다.
- 방문 여부는 자원과 현재 branch를 함께 추적한다. 같은 자원이 다른 branch에서 필요하면 계속 순회한다.
- include를 요청하지 않으면 included를 생략하고, 요청했지만 관계가 비면 빈 목록을 유지한다. controller의 명시적 `include=` 처리도 함께 검토한다.

### 안전한 값과 식별자

- 모델 또는 값 객체는 null이 아닌 id를 가져야 한다. type_name과 문자열화한 id가 JSON:API 식별자다.
- attributes는 None·문자열·정수·불리언·UUID·StrEnum·datetime·유한 실수와 이 값의 list·문자열 key dict만 허용한다.
- NaN·infinity·tuple·임의 객체·문자열이 아닌 dict key는 JsonApiSerializationError로 거부한다. 중복 camelCase attribute 이름도 거부한다.

### Testing Requirements

`TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리킬 때 `uv run pytest --no-cov tests/serializers tests/jsonapi/test_query.py -q`를 실행한다. URL이 없으면 `./scripts/check.sh`의 임시 Docker PostgreSQL 게이트를 사용한다.

- `tests/serializers/test_example_serializer.py`: attributes·관계·include 중복·순환·안전한 값·미적재 관계와 loader.
- `tests/serializers/test_auth_serializer.py`: 사용자 공개 필드·고정 me 링크·토큰 쌍 표현.
- loader나 직렬화 실패 처리 변경은 controller의 create/PATCH/PUT rollback 회귀도 함께 확인한다.

### Common Patterns

관계 선언은 MappingProxyType과 frozen RelationshipDefinition으로 고정한다. 한 문서의 primary key·included·visited 상태는 SerializationContext에서 공유하고 SQLAlchemy selectinload를 선언에서 도출한다.

## Dependencies

- 내부: [models](../models/AGENTS.md), [auth](../auth/AGENTS.md)의 AuthTokenResource, [JSON:API](../jsonapi/AGENTS.md) 문서, [controller concern](../controllers/concerns/AGENTS.md).
- 외부: SQLAlchemy 2 inspection·selectinload, Pydantic TypeAdapter, FastAPI jsonable_encoder.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->

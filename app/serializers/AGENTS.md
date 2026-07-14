# Serializer 하위 지침

이 문서는 `app/serializers/`의 선언형 직렬화 동작과 회귀 검증 기준을 정한다.

## 선언

- 각 `JsonApiSerializer`는 `type_name`, 공개 `attributes`, 필요한 `relationships`를 명시한다.
- `attributes`에는 내부 snake_case 이름만 선언한다. 선언하지 않은 모델 필드는 읽거나 내보내지 않으며, 공개 이름은 snake-to-camel 변환을 따른다.
- `RelationshipDefinition`은 공개 관계 이름에 내부 `attribute`, 대상 `serializer`, `many` cardinality를 함께 고정한다.
- 선언한 관계는 실제 ORM 관계여야 하며, ORM cardinality와 `many` 값이 일치해야 한다.
- to-many 값은 순차 컬렉션이어야 하고, to-one 값은 하나의 모델 또는 `None`이어야 한다.
- `resource_path`는 자원 `self` 링크와 관계의 `self`·`related` 링크를 내보낼 serializer를 정한다. `None`이면 해당 링크를 만들지 않는다.
- 새 공개 serializer와 공용 기반 타입은 `serializers/__init__.py`에서 export한다.

## 로더와 관계 값

- 관계 linkage는 include가 없어도 모든 선언 관계를 읽으므로 조회문에는 `required_loader_paths()`의 모든 경로를 `loader_options()`로 eager-load한다.
- 요청한 중첩 include는 선언된 관계를 따라 추가 loader 경로를 만들며, route에서 별도 지연 로딩으로 이를 우회하지 않는다.
- persistent 또는 detached 모델의 미적재 관계는 조회를 발생시키지 말고 `JsonApiSerializationError`로 실패시킨다.
- `initialize_relationship_defaults()`는 transient 또는 pending 모델의 미적재 관계에만 to-one `None`, to-many `[]`를 설정한다.
- persistent 또는 detached 모델에는 기본 관계 값을 주입하지 않는다.

## Include 순회

- include 경로의 각 segment는 비어 있지 않아야 하며, 모든 단계가 serializer에 선언된 관계여야 한다.
- include 트리는 중복 경로를 합치고 임의의 숫자 깊이 제한을 두지 않는다.
- `included`에는 primary resource를 다시 넣지 않는다.
- 같은 `(type, id)` 보조 자원은 처음 발견한 순서를 유지하며 한 번만 넣는다.
- 순환 종료는 자원과 현재 branch를 함께 추적한다. 같은 자원이 다른 branch에서 필요한 경우는 계속 순회한다.
- include가 요청되지 않으면 `included` 멤버를 만들지 않는다. 요청했지만 관계가 비어 있으면 빈 목록을 유지한다.

## 안전한 값과 식별자

- 모델에는 null이 아닌 `id`가 있어야 하며, `type_name`과 문자열화한 `id`가 자원 식별자가 된다.
- attribute 값은 `None`, 문자열, 정수, 불리언, `UUID`, `StrEnum`, `datetime`, 유한한 실수와 이 값들로만 구성된 list·dict만 허용한다.
- dict 키는 문자열이어야 하며, `NaN`, infinity, tuple, 임의 객체 등 안전하지 않은 값은 `JsonApiSerializationError`로 거부한다.

## 검증

- serializer 변경은 `uv run pytest --no-cov tests/serializers/test_example_serializer.py -q`로 확인한다.
- 조회의 loader 회귀는 `tests/jsonapi/test_query.py`의 serializer loader 적용 테스트도 함께 확인한다.

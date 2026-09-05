"""Example category query policy."""

from app.jsonapi.query import FilterField, QueryPolicy, SortTerm
from app.models import ExampleCategory

EXAMPLE_CATEGORY_QUERY_POLICY = QueryPolicy(
    filters={
        "name": FilterField(
            column=ExampleCategory.name,
            parser=str,
            operators=frozenset({"exact", "contains"}),
        ),
    },
    sorts={
        "name": ExampleCategory.name,
        "createdAt": ExampleCategory.created_at,
    },
    includes=frozenset(),
    # 선택기는 알파벳순이 맞다. Example의 기본 정렬(createdAt DESC)과 다른 것은
    # 의도된 것이다 — 참조 데이터는 최신순으로 고르지 않는다.
    default_sort=(SortTerm("name"),),
    tie_breaker=SortTerm("id", column=ExampleCategory.id),
)
"""Read-only query allowlist for example categories.

**인덱스 판단 — 만들지 않는다.** 정본의 Example 정책과 달리 여기서는 정렬이
인덱스로 완전히 커버되지 **않는다**. `name`의 UNIQUE 인덱스가 `name` 순서를
주지만, PostgreSQL은 유니크 제약을 근거로 뒤따르는 정렬 키를 지우지 않으므로
`ORDER BY name, id` 계획에는 incremental sort가 남는다.

그럼에도 `(name, id)` 인덱스를 만들지 않는 이유는 둘이다. `name`이 유니크해서
동점 그룹의 크기가 항상 1이라 그 정렬 단계가 실질적으로 하는 일이 없고, 참조
테이블의 행 수가 작다(분류·라벨 각각 수십 개 규모). `createdAt`은 유니크하지
않으므로 첫 번째 이유는 적용되지 않는다 — 같은 트랜잭션에서 커밋된 행은
`now()` 값을 공유해 동점 그룹이 테이블 전체가 될 수 있다. 그럼에도 행 수가
작다는 두 번째 이유만으로 결론은 같아 `createdAt` 정렬에도 인덱스를 만들지
않는다. 행 수가 크게 늘어 이 목록이 주된 부하가 되면 그때 `(name, id)`를 같은
규칙으로 판단해 추가한다.

**`tests/integration/test_query_indexes.py`의 Example 테스트를 이 자원에 복사하지
않는다.** 그 테스트는 계획에 `Sort` 노드가 없음을 단언하는데, 위 이유로 여기서는
그 단언이 참이 아니다.

`includes`가 비어 있는 것도 의도된 것이다. `examples` 역참조를 열면
Example → category → examples → … 로 순환이 생긴다.
"""

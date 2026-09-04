"""Example tag query policy."""

from app.jsonapi.query import FilterField, QueryPolicy, SortTerm
from app.models import ExampleTag

EXAMPLE_TAG_QUERY_POLICY = QueryPolicy(
    filters={
        "name": FilterField(
            column=ExampleTag.name,
            parser=str,
            operators=frozenset({"exact", "contains"}),
        ),
    },
    sorts={
        "name": ExampleTag.name,
        "createdAt": ExampleTag.created_at,
    },
    includes=frozenset(),
    default_sort=(SortTerm("name"),),
    tie_breaker=SortTerm("id", column=ExampleTag.id),
)
"""Read-only query allowlist for example tags.

**인덱스 판단 — 만들지 않는다.** 근거는 `EXAMPLE_CATEGORY_QUERY_POLICY`와 같다.
`name`의 UNIQUE 인덱스가 `name` 순서를 주지만 `ORDER BY name, id`에는 incremental
sort가 남는다. `name`이 유니크해서 동점 그룹이 항상 1이고 라벨 수가 적으므로
`(name, id)` 인덱스를 만들지 않는다. `createdAt`도 같다.

`includes`가 비어 있는 것은 `examples` 역참조가 순환을 만들기 때문이다.
"""

# 참조 자원 읽기 라우트 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `CrudActions`에 읽기 전용 옵션을 추가하고, 그 위에 `/api/v1/categories`와 `/api/v1/tags` 읽기 라우트를 올린다.

**Architecture:** `CrudActions`는 지금 create/update/delete를 무조건 등록하고 선택 가능한 것은 `enable_upsert` 하나뿐이다. 같은 모양의 `enable_writes` 옵션을 더해 읽기 라우트만 등록할 수 있게 하고, 그 위에 두 참조 자원 컨트롤러를 선언만으로 얹는다. 자원별 service 계층은 만들지 않는다 — 이 저장소의 명시적 비목표다.

**Tech Stack:** FastAPI, SQLAlchemy 2, PostgreSQL, Pydantic, pytest, uv, poe

**Spec:** `../../../../template-typescript-nextjs/docs/superpowers/specs/2026-09-04-nextjs-jsonapi-template-design.md` 6장

## Global Constraints

- URL 경로는 `/api/v1/categories`, `/api/v1/tags`다.
- JSON:API `type_name`은 기존 `exampleCategories`, `exampleTags`를 **바꾸지 않는다.** 이미 발행된 관계 linkage의 `type`이다.
- 쓰기 라우트를 만들지 않는다. 관계 라우트도 만들지 않는다.
- 조회 정책: filters `name` [`exact`, `contains`] / sorts `name`, `createdAt` / default `name ASC` / tie breaker `id ASC` / includes 없음.
- **새 인덱스를 만들지 않는다.** `name`이 `UNIQUE`라 `(name, id)` 정렬이 기존 인덱스로 커버된다. 만들지 않기로 한 근거를 정책 선언부 주석에 남긴다 — "정렬을 여는 변경은 인덱스를 진다"는 저장소 규칙이 근거 기록을 요구한다.
- 스키마 변경이 없다. 마이그레이션을 만들지 않는다 — `categories`, `tags` 테이블은 이미 있다.
- 등록은 전부 손으로 한다. 자동 탐색을 추가하지 않는다.
- `fields[...]` 희소 필드셋을 추가하지 않는다.
- 자원별 repository 또는 service 계층을 만들지 않는다.
- 검증 게이트는 `uv run poe check`(= `./scripts/check.sh`) 하나다.

---

### Task 1: `CrudActions`에 `enable_writes` 옵션

**Files:**
- Modify: `app/controllers/concerns/crud_base.py:42`
- Modify: `app/controllers/concerns/crud_actions.py:76-141`
- Modify: `app/controllers/concerns/route_registrar.py:59-152`
- Test: `tests/controllers/test_crud_actions.py`

**Interfaces:**
- Consumes: 없음 (첫 작업)
- Produces:
  - `CrudDeclarations.enable_writes: bool` — 클래스 속성, 기본 `True`
  - `register_resource_routes(..., enable_writes: bool, create_document_schema: type[BaseModel] | None, update_document_schema: type[BaseModel] | None, replace_document_schema: type[BaseModel] | None)` — 세 스키마 인자가 `None`을 받게 된다
  - `CrudActions._writable_relationship_names`가 `enable_writes=False`일 때 항상 `frozenset()`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/controllers/test_crud_actions.py`의 `RollbackController` 클래스 정의 바로 뒤에 컨트롤러를 추가한다.

```python
class ReadOnlyExampleController(CrudActions[Example, BaseModel, BaseModel, BaseModel]):
    """쓰기 스키마를 하나도 선언하지 않는 읽기 전용 컨트롤러.

    ``relationships_schema``를 일부러 선언한다 — ``enable_writes = False``가
    관계 쓰기 라우트까지 막지 못하면 이 선언이 그 구멍을 드러낸다.
    """

    model_class = Example
    serializer_class = ExampleSerializer
    relationships_schema = ExampleRelationships
    query_policy = EXAMPLE_QUERY_POLICY
    enable_writes = False
```

같은 파일 맨 끝에 테스트를 추가한다.

```python
def test_read_only_controller_registers_only_read_routes() -> None:
    controller = ReadOnlyExampleController(prefix="/api/v1/examples", tags=["examples"])

    registered = {
        (route.path, method)
        for route in controller.router.routes
        for method in route.methods  # type: ignore[attr-defined]
    }

    assert registered == {
        ("/api/v1/examples", "GET"),
        ("/api/v1/examples/{resource_id}", "GET"),
        ("/api/v1/examples/{resource_id}/relationships/category", "GET"),
        ("/api/v1/examples/{resource_id}/category", "GET"),
        ("/api/v1/examples/{resource_id}/relationships/tags", "GET"),
        ("/api/v1/examples/{resource_id}/tags", "GET"),
    }


def test_read_only_controller_has_no_writable_relationship_names() -> None:
    controller = ReadOnlyExampleController(prefix="/api/v1/examples", tags=["examples"])

    # 선언된 relationships_schema가 있어도 비어 있어야 한다. 비어 있지 않으면
    # register_relationship_routes가 mutation 라우트를 등록한다.
    assert controller._writable_relationship_names == frozenset()


def test_write_controller_still_registers_every_route() -> None:
    controller = ExampleCrudController(prefix="/api/v1/examples", tags=["examples"])

    methods = {
        (route.path, method)
        for route in controller.router.routes
        for method in route.methods  # type: ignore[attr-defined]
    }

    assert ("/api/v1/examples", "POST") in methods
    assert ("/api/v1/examples/{resource_id}", "PATCH") in methods
    assert ("/api/v1/examples/{resource_id}", "DELETE") in methods
    assert ("/api/v1/examples/{resource_id}/relationships/tags", "POST") in methods
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `uv run pytest tests/controllers/test_crud_actions.py -k read_only -v`

Expected: FAIL. `ReadOnlyExampleController` 정의 시점 또는 인스턴스화 시점에 터진다 — `enable_writes`가 아직 없고, `__init__`이 `self.create_schema`를 읽으려다 `AttributeError`를 낸다.

- [ ] **Step 3: 선언 계약에 `enable_writes`를 추가한다**

`app/controllers/concerns/crud_base.py`의 `enable_upsert = False` 줄 **위**에 추가한다.

```python
    enable_writes = True
    """쓰기 라우트를 등록할지 여부.

    ``False``면 create/update/upsert/destroy와 관계 mutation 라우트가 등록되지
    않고, ``create_schema``·``update_schema``·``replace_schema``를 선언하지 않아도
    된다. 참조 데이터처럼 서버가 관리하는 자원을 위한 것이다.
    """
```

- [ ] **Step 4: 라우트 등록기가 옵션을 존중하게 한다**

`app/controllers/concerns/route_registrar.py`의 `register_resource_routes` 시그니처에서 세 문서 스키마의 타입을 바꾸고 `enable_writes`를 추가한다.

```python
def register_resource_routes(
    router: APIRouter,
    *,
    controller_name: str,
    read_dependencies: Sequence[Callable[..., Any]],
    write_dependencies: Sequence[Callable[..., Any]],
    enable_writes: bool,
    enable_upsert: bool,
    index: IndexAction,
    show: ShowAction,
    create: CreateAction,
    update: WriteAction,
    upsert: WriteAction,
    destroy: DestroyAction,
    create_document_schema: type[BaseModel] | None,
    update_document_schema: type[BaseModel] | None,
    replace_document_schema: type[BaseModel] | None,
) -> None:
```

그 다음, 문서 순서를 유지한 채 쓰기 라우트만 감싼다. `GET ""` 등록 뒤의 `POST ""` 등록 블록을 아래처럼 조건 안으로 옮기고, `GET "/{resource_id}"` 등록 뒤의 `PATCH`·`PUT`·`DELETE` 블록도 같은 조건 안으로 옮긴다.

```python
    # POST는 GET ""과 GET "/{resource_id}" 사이에 있어야 한다 — 이 순서가
    # OpenAPI 문서의 operation 순서를 정하므로 재배치하지 않는다.
    if enable_writes:
        assert create_document_schema is not None
        router.add_api_route(
            "",
            _create_delegate(controller_name, create, create_document_schema),
            methods=["POST"],
            response_class=JsonApiResponse,
            response_model=SuccessDocument,
            status_code=201,
            responses=_write_error_responses(write_dependencies, 400, 403, 406, 409, 415, 422, 500),
            dependencies=[Depends(dependency) for dependency in write_dependencies],
            name=f"{controller_name}.create",
        )
```

`PATCH`·`PUT`·`DELETE`를 감싸는 블록은 이렇게 된다.

```python
    if enable_writes:
        assert update_document_schema is not None
        assert replace_document_schema is not None
        router.add_api_route(
            "/{resource_id}",
            _write_delegate(f"{controller_name}_update", update, update_document_schema),
            methods=["PATCH"],
            response_class=JsonApiResponse,
            response_model=SuccessDocument,
            responses=_write_error_responses(write_dependencies, 400, 404, 406, 409, 415, 422, 500),
            dependencies=[Depends(dependency) for dependency in write_dependencies],
            name=f"{controller_name}.update",
        )
        if enable_upsert:
            router.add_api_route(
                "/{resource_id}",
                _write_delegate(f"{controller_name}_upsert", upsert, replace_document_schema),
                methods=["PUT"],
                response_class=JsonApiResponse,
                response_model=SuccessDocument,
                responses={
                    201: {
                        "description": "Resource created",
                        "model": SuccessDocument,
                        "headers": {
                            "Location": {
                                "description": "Canonical URL of the created resource",
                                "schema": {"type": "string"},
                            }
                        },
                    },
                    **_write_error_responses(write_dependencies, 400, 404, 406, 409, 415, 422, 500),
                },
                dependencies=[Depends(dependency) for dependency in write_dependencies],
                name=f"{controller_name}.upsert",
            )
        router.add_api_route(
            "/{resource_id}",
            _destroy_delegate(controller_name, destroy),
            methods=["DELETE"],
            status_code=204,
            response_class=JsonApiResponse,
            responses=_write_error_responses(write_dependencies, 400, 404, 406, 422, 500),
            dependencies=[Depends(dependency) for dependency in write_dependencies],
            name=f"{controller_name}.destroy",
        )
```

`register_relationship_routes`는 **고치지 않는다.** mutation 라우트는 이미 `if public_name not in writable_names: continue`로 걸러지고, 다음 단계가 `writable_names`를 빈 집합으로 만든다.

- [ ] **Step 5: `CrudActions.__init__`이 쓰기 조립을 건너뛰게 한다**

`app/controllers/concerns/crud_actions.py`의 `__init__`에서 세 스키마 조립 블록을 조건 안으로 넣는다.

```python
        self._create_document_schema: type[BaseModel] | None = None
        self._update_document_schema: type[BaseModel] | None = None
        self._replace_document_schema: type[BaseModel] | None = None
        if self.enable_writes:
            self._create_document_schema = write_document_model(
                name=f"{type(self).__name__}Create",
                attributes_schema=self.create_schema,
                require_attributes=True,
                require_id=False,
                relationships_schema=self.relationships_schema,
            )
            self._update_document_schema = write_document_model(
                name=f"{type(self).__name__}Update",
                attributes_schema=self.update_schema,
                require_attributes=False,
                require_id=True,
                relationships_schema=self.relationships_schema,
            )
            self._replace_document_schema = write_document_model(
                name=f"{type(self).__name__}Replace",
                attributes_schema=self.replace_schema,
                require_attributes=True,
                require_id=True,
                relationships_schema=self.relationships_schema,
            )
```

`_writable_relationship_names` 조립을 아래로 바꾼다.

```python
        # 읽기 전용 자원은 relationships_schema를 선언했더라도 쓰기 관계 라우트를
        # 갖지 않는다. 이 빈 집합이 register_relationship_routes의 mutation 등록을
        # 막는 유일한 장치이므로 조건을 여기서 건다.
        self._writable_relationship_names = (
            frozenset(
                field.alias or snake_to_camel(field_name)
                for field_name, field in (
                    self.relationships_schema.model_fields.items() if self.relationships_schema is not None else ()
                )
            )
            if self.enable_writes
            else frozenset()
        )
```

`register_resource_routes` 호출에 인자를 추가한다.

```python
        register_resource_routes(
            self.router,
            controller_name=type(self).__name__,
            read_dependencies=self.read_dependencies,
            write_dependencies=self.write_dependencies,
            enable_writes=self.enable_writes,
            enable_upsert=self.enable_upsert,
            index=self.index,
            show=self.show,
            create=self.create,
            update=self.update,
            upsert=self.upsert,
            destroy=self.destroy,
            create_document_schema=self._create_document_schema,
            update_document_schema=self._update_document_schema,
            replace_document_schema=self._replace_document_schema,
        )
```

- [ ] **Step 6: 테스트가 통과하는 것을 확인한다**

Run: `uv run pytest tests/controllers/test_crud_actions.py -v`

Expected: PASS. 새 테스트 3개를 포함해 이 파일 전체가 통과한다.

- [ ] **Step 7: 타입 검사와 린트**

Run: `uv run poe typecheck && uv run poe lint`

Expected: 둘 다 통과.

- [ ] **Step 8: 커밋**

```bash
git add app/controllers/concerns/crud_base.py app/controllers/concerns/crud_actions.py app/controllers/concerns/route_registrar.py tests/controllers/test_crud_actions.py
git commit -m "feat: add read-only mode to CrudActions

enable_upsert와 같은 모양의 enable_writes 옵션을 더한다. False면
create/update/upsert/destroy와 관계 mutation 라우트를 등록하지 않고,
쓰기 스키마를 선언하지 않아도 된다.

relationships_schema를 선언한 읽기 전용 자원도 쓰기 관계 라우트를 갖지
않도록 _writable_relationship_names를 빈 집합으로 고정한다."
```

---

### Task 2: 참조 자원 시리얼라이저에 `resource_path` 부여

**Files:**
- Modify: `app/serializers/example_category_serializer.py`
- Modify: `app/serializers/example_tag_serializer.py`
- Test: `tests/serializers/test_example_serializer.py`

**Interfaces:**
- Consumes: 없음 (Task 1과 독립)
- Produces: `ExampleCategorySerializer.resource_path == "/api/v1/categories"`, `ExampleTagSerializer.resource_path == "/api/v1/tags"`. 두 자원이 `included[]`에 실릴 때 `links.self`가 붙는다.

**주의:** 이것은 조용한 추가가 아니라 **관측 가능한 계약 변경**이다. `GET /api/v1/examples?include=category,tags` 응답의 `included[]`가 바뀐다. 기존 기대값을 같은 커밋에서 갱신한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/serializers/test_example_serializer.py` 맨 끝에 추가한다. 이 파일에 이미 있는 `example` fixture를 쓴다 — 분류 id는 `UUID(int=10)`, 라벨 id는 `UUID(int=20)`이다. 새 import가 필요 없다.

```python
def test_included_reference_resources_carry_self_links(example: Example) -> None:
    document = ExampleSerializer.document(example, include=("category", "tags"))

    links = {item.type: item.links["self"] for item in document.included}

    assert links == {
        "exampleCategories": f"/api/v1/categories/{UUID(int=10)}",
        "exampleTags": f"/api/v1/tags/{UUID(int=20)}",
    }
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `uv run pytest tests/serializers/test_example_serializer.py::test_included_reference_resources_carry_self_links -v`

Expected: FAIL. `resource_path`가 `None`이라 `links`가 없다.

- [ ] **Step 3: 두 시리얼라이저에 경로를 준다**

`app/serializers/example_category_serializer.py`:

```python
class ExampleCategorySerializer(JsonApiSerializer[ExampleCategory]):
    """Expose the public example-category representation."""

    type_name = "exampleCategories"
    resource_path = "/api/v1/categories"
    attributes = ("name",)
```

`app/serializers/example_tag_serializer.py`:

```python
class ExampleTagSerializer(JsonApiSerializer[ExampleTag]):
    """Expose the public example-tag representation."""

    type_name = "exampleTags"
    resource_path = "/api/v1/tags"
    attributes = ("name",)
```

`type_name`은 건드리지 않는다 — 이미 발행된 관계 linkage의 `type`이다.

- [ ] **Step 4: 테스트가 통과하는 것을 확인한다**

Run: `uv run pytest tests/serializers/test_example_serializer.py -v`

Expected: 새 테스트는 PASS. 이 파일의 다른 테스트가 `included[]`를 통째로 비교하고 있었다면 여기서 실패한다.

- [ ] **Step 5: 깨진 기대값을 갱신한다**

`tests/serializers/test_example_serializer.py`의 `test_auxiliary_included_resources_have_only_type_id_and_name`이 `included[]`의 덤프를 통째로 비교하므로 반드시 깨진다. 이름이 계약을 잘못 말하게 되었으므로 이름도 함께 바꾼다.

```python
def test_auxiliary_included_resources_expose_type_id_name_and_self_link(example: Example) -> None:
    document = ExampleSerializer.document(example, include=("category", "tags"))

    assert [item.model_dump(mode="json") for item in document.included] == [
        {
            "type": "exampleCategories",
            "id": str(UUID(int=10)),
            "attributes": {"name": "examples"},
            "links": {"self": f"/api/v1/categories/{UUID(int=10)}"},
        },
        {
            "type": "exampleTags",
            "id": str(UUID(int=20)),
            "attributes": {"name": "filing"},
            "links": {"self": f"/api/v1/tags/{UUID(int=20)}"},
        },
    ]
```

그다음 나머지 스위트를 돌려 남은 실패를 찾는다.

Run: `uv run pytest tests/ -q`

`included[]`나 관계 응답을 단언하는 파일은 아래 넷이다. 남은 실패는 이 중에서만 나온다.

```text
tests/serializers/test_example_serializer.py
tests/controllers/test_relationship_actions.py
tests/controllers/test_crud_actions.py
tests/test_example_controller.py
```

실패한 단언마다 기대값에 `links.self`를 더한다. 값은 `/api/v1/categories/{id}`와 `/api/v1/tags/{id}`다. **기대값만 고친다** — 실패를 없애려고 시리얼라이저를 되돌리지 않는다.

- [ ] **Step 6: 전체 테스트가 통과하는 것을 확인한다**

Run: `uv run pytest tests/ -q`

Expected: 전부 PASS.

- [ ] **Step 7: 커밋**

```bash
git add app/serializers/example_category_serializer.py app/serializers/example_tag_serializer.py tests/
git commit -m "feat: give reference resources canonical self links

exampleCategories와 exampleTags에 resource_path를 준다. included[]에 실릴
때 links.self가 붙으므로 기존 응답이 바뀐다 — 기대값을 같은 변경에서
갱신한다.

type_name은 그대로 둔다. 이미 발행된 관계 linkage의 type이다."
```

---

### Task 3: 두 참조 자원 컨트롤러와 라우트 등록

**Files:**
- Create: `app/schemas/example_category.py`
- Create: `app/schemas/example_tag.py`
- Modify: `app/schemas/__init__.py`
- Create: `app/controllers/api/v1/example_categories_controller.py`
- Create: `app/controllers/api/v1/example_tags_controller.py`
- Modify: `app/controllers/api/v1/__init__.py`
- Modify: `config/routes.py`
- Test: `tests/config/test_routes.py`
- Test: `tests/test_example_controller.py` (라우트·OpenAPI 인벤토리 갱신 — Step 9)

**Interfaces:**
- Consumes: Task 1의 `CrudDeclarations.enable_writes`, Task 2의 `resource_path`
- Produces: `EXAMPLE_CATEGORY_QUERY_POLICY`, `EXAMPLE_TAG_QUERY_POLICY` (`app.schemas`에서 export), `ExampleCategoriesController`, `ExampleTagsController` (`app.controllers.api.v1`에서 export)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/config/test_routes.py`의 `test_routes_expose_expected_crud_controllers`를 고친다.

```python
def test_routes_expose_expected_crud_controllers() -> None:
    composed = [type(controller).__name__ for controller in _composed_crud_controllers()]

    assert composed == [
        "ExamplesController",
        "ExampleCategoriesController",
        "ExampleTagsController",
    ]
```

같은 파일 맨 끝에 추가한다.

```python
def test_reference_resource_controllers_are_read_only() -> None:
    read_only = {"ExampleCategoriesController", "ExampleTagsController"}

    for controller in _composed_crud_controllers():
        if type(controller).__name__ not in read_only:
            continue
        methods = {
            method
            for route in controller.router.routes
            for method in route.methods  # type: ignore[attr-defined]
        }
        assert methods == {"GET"}
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `uv run pytest tests/config/test_routes.py -v`

Expected: FAIL. 구성된 컨트롤러가 `["ExamplesController"]` 하나뿐이다.

- [ ] **Step 3: 조회 정책을 쓴다**

`app/schemas/example_category.py`를 만든다.

```python
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

**인덱스 판단.** 모든 정렬 뒤에 ``id ASC``가 붙으므로 유용한 인덱스는
``(<컬럼>, id)``다. ``name``은 ``UNIQUE``라 이미 인덱스가 있고, 그 인덱스가
``(name, id)`` 정렬을 이끈다 — 새 인덱스를 **만들지 않는다**. ``createdAt``
정렬에도 인덱스를 만들지 않는다: 참조 데이터는 행 수가 적어(분류 소수, 라벨
소수) 플래너가 순차 스캔을 골라도 비용이 낮다. 행 수가 크게 늘고 ``createdAt``
정렬이 주된 부하가 되면 그때 ``(created_at, id)``를 같은 규칙으로 판단해
추가한다.

``includes``가 비어 있는 것도 의도된 것이다. ``examples`` 역참조를 열면
Example → category → examples → … 로 순환이 생긴다.
"""
```

`app/schemas/example_tag.py`를 만든다.

```python
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

**인덱스 판단.** ``name``이 ``UNIQUE``라 이미 인덱스가 있고 그 인덱스가
``(name, id)`` 정렬을 이끈다 — 새 인덱스를 **만들지 않는다**. ``createdAt``도
같은 이유로 만들지 않는다: 라벨 수가 적어 순차 스캔의 비용이 낮다.

``includes``가 비어 있는 것은 ``examples`` 역참조가 순환을 만들기 때문이다.
"""
```

- [ ] **Step 4: 스키마 패키지에서 내보낸다**

`app/schemas/__init__.py`에 두 정책을 추가한다. 기존 import 줄들 아래에 더하고 `__all__`에도 넣는다.

```python
from app.schemas.example_category import EXAMPLE_CATEGORY_QUERY_POLICY
from app.schemas.example_tag import EXAMPLE_TAG_QUERY_POLICY
```

`__all__`에 `"EXAMPLE_CATEGORY_QUERY_POLICY"`, `"EXAMPLE_TAG_QUERY_POLICY"`를 알파벳 순서에 맞게 넣는다.

- [ ] **Step 5: 컨트롤러를 쓴다**

`app/controllers/api/v1/example_categories_controller.py`:

```python
"""Example category read-only JSON:API resource controller."""

from pydantic import BaseModel

from app.controllers.concerns import CrudActions
from app.models import ExampleCategory
from app.schemas import EXAMPLE_CATEGORY_QUERY_POLICY
from app.serializers import ExampleCategorySerializer


class ExampleCategoriesController(CrudActions[ExampleCategory, BaseModel, BaseModel, BaseModel]):
    """Expose example categories as a read-only collection.

    분류는 서버가 관리하는 참조 데이터다. 쓰기 라우트를 열지 않으므로
    ``create_schema``·``update_schema``·``replace_schema``를 선언하지 않는다 —
    ``enable_writes = False``가 그 셋을 읽지 않게 한다.

    이 자원이 존재하는 이유는 관계 선택기다. 분류는 Example의 관계로만
    노출되어 있어서, 폼이 고를 목록을 가져올 곳이 없었다.
    """

    model_class = ExampleCategory
    serializer_class = ExampleCategorySerializer
    query_policy = EXAMPLE_CATEGORY_QUERY_POLICY
    enable_writes = False
```

`app/controllers/api/v1/example_tags_controller.py`:

```python
"""Example tag read-only JSON:API resource controller."""

from pydantic import BaseModel

from app.controllers.concerns import CrudActions
from app.models import ExampleTag
from app.schemas import EXAMPLE_TAG_QUERY_POLICY
from app.serializers import ExampleTagSerializer


class ExampleTagsController(CrudActions[ExampleTag, BaseModel, BaseModel, BaseModel]):
    """Expose example tags as a read-only collection.

    라벨은 서버가 관리하는 참조 데이터다. 쓰기 라우트를 열지 않는 이유와
    이 자원이 존재하는 이유는 ``ExampleCategoriesController``와 같다.
    """

    model_class = ExampleTag
    serializer_class = ExampleTagSerializer
    query_policy = EXAMPLE_TAG_QUERY_POLICY
    enable_writes = False
```

- [ ] **Step 6: 컨트롤러 패키지에서 내보낸다**

`app/controllers/api/v1/__init__.py`를 아래로 바꾼다.

```python
"""Version 1 API controllers."""

from app.controllers.api.v1.auth_controller import AuthController
from app.controllers.api.v1.example_categories_controller import ExampleCategoriesController
from app.controllers.api.v1.example_tags_controller import ExampleTagsController
from app.controllers.api.v1.examples_controller import ExamplesController
from app.controllers.api.v1.users_controller import UsersController

__all__ = [
    "AuthController",
    "ExampleCategoriesController",
    "ExampleTagsController",
    "ExamplesController",
    "UsersController",
]
```

- [ ] **Step 7: 라우트를 등록한다**

`config/routes.py`를 아래로 바꾼다. 컨트롤러를 모듈 변수로 두는 형태를 유지한다 — `tests/config/test_routes.py`가 그 변수들을 훑어 검사한다.

```python
"""Explicit application route composition."""

from fastapi import APIRouter

from app.controllers.api.v1 import (
    AuthController,
    ExampleCategoriesController,
    ExampleTagsController,
    ExamplesController,
    UsersController,
)
from app.controllers.health_controller import HealthController

api_router = APIRouter()
auth_controller = AuthController(prefix="/api/v1/auth", tags=["authentication"])
examples_controller = ExamplesController(prefix="/api/v1/examples", tags=["examples"])
example_categories_controller = ExampleCategoriesController(
    prefix="/api/v1/categories", tags=["example categories"]
)
example_tags_controller = ExampleTagsController(prefix="/api/v1/tags", tags=["example tags"])
health_controller = HealthController(tags=["health"])
users_controller = UsersController(prefix="/api/v1/users", tags=["users"])
api_router.include_router(auth_controller.router)
api_router.include_router(examples_controller.router)
api_router.include_router(example_categories_controller.router)
api_router.include_router(example_tags_controller.router)
api_router.include_router(health_controller.router)
api_router.include_router(users_controller.router)
```

- [ ] **Step 8: 테스트가 통과하는 것을 확인한다**

Run: `uv run pytest tests/config/test_routes.py -v`

Expected: PASS. `test_serializer_resource_path_matches_composed_prefix`도 통과한다 — Task 2가 `resource_path`를 각 prefix와 같게 맞춰 놓았다.

- [ ] **Step 9: 라우트·OpenAPI 인벤토리를 갱신한다**

`tests/test_example_controller.py`에 애플리케이션의 전체 표면을 고정하는 테스트 다섯 개가 있다. 라우트가 넷 늘었으므로 전부 실패한다.

```text
test_openapi_exposes_only_declared_application_operations
test_openapi_operation_ids_and_route_names_are_stable
test_openapi_component_schema_names_are_stable
test_application_exposes_only_explicitly_composed_routes
test_every_application_route_uses_the_shared_jsonapi_assembly
```

Run: `uv run pytest tests/test_example_controller.py -q`

실패한 인벤토리마다 아래 여덟을 더한다. operation id와 route name은 `route_registrar`가 `f"{controller_name}.index"` 형식으로 만든다.

```text
GET /api/v1/categories                 ExampleCategoriesController.index
GET /api/v1/categories/{resource_id}   ExampleCategoriesController.show
GET /api/v1/tags                       ExampleTagsController.index
GET /api/v1/tags/{resource_id}         ExampleTagsController.show
```

`test_openapi_component_schema_names_are_stable`에는 **아무것도 더하지 않는다.** 두 컨트롤러는 `enable_writes = False`라 쓰기 문서 스키마를 만들지 않으므로 새 component가 생기지 않는다. 이 테스트가 새 이름을 요구하면 Task 1의 조건이 제대로 걸리지 않았다는 뜻이다.

Run: `uv run pytest tests/test_example_controller.py -q`

Expected: PASS.

- [ ] **Step 10: 타입 검사와 린트**

Run: `uv run poe typecheck && uv run poe lint && uv run poe format-check`

Expected: 전부 통과.

- [ ] **Step 11: 커밋**

```bash
git add app/schemas/example_category.py app/schemas/example_tag.py app/schemas/__init__.py app/controllers/api/v1/ config/routes.py tests/config/test_routes.py
git commit -m "feat: add read-only categories and tags routes

GET /api/v1/categories와 GET /api/v1/tags를 연다. 분류와 라벨이 Example의
관계로만 노출되어 있어서 폼의 관계 선택기가 고를 목록을 가져올 곳이 없었다.

정렬 기본값은 name ASC다. 참조 데이터는 최신순으로 고르지 않는다.
name이 UNIQUE라 (name, id) 정렬이 기존 인덱스로 커버되므로 새 인덱스를
만들지 않는다 — 근거를 정책 선언부에 남겼다."
```

---

### Task 4: 통합 검증과 문서

**Files:**
- Create: `tests/integration/test_reference_resources.py`
- Modify: `README.md`
- Modify: `app/AGENTS.md`
- Test: 위 통합 테스트

**Interfaces:**
- Consumes: Task 3의 `/api/v1/categories`, `/api/v1/tags` 라우트
- Produces: 없음 (마지막 작업)

- [ ] **Step 1: 통합 테스트를 쓴다**

`tests/integration/test_reference_resources.py`를 만든다. fixture는 `tests/conftest.py`가 주는 `client`(테스트 DB에 연결된 `TestClient`)와 `committed_session`(다른 커넥션에서도 보이도록 커밋하는 세션)을 쓴다. `db_session`은 쓰지 **않는다** — 자기 커넥션의 savepoint 안에서만 살아 있어서 요청 세션이 그 행을 보지 못한다. `tests/test_example_controller.py`가 같은 짝을 쓴다.

```python
"""Read-only reference resource route integration tests."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.jsonapi import JSONAPI_MEDIA_TYPE
from app.models import ExampleCategory, ExampleTag

HEADERS = {"Accept": JSONAPI_MEDIA_TYPE}


def _persist_reference_data(session: Session) -> None:
    session.add_all(
        [
            # 이름을 ASCII로 두는 이유: 정렬 기대값이 PostgreSQL의 대조 규칙에
            # 의존하지 않게 하려는 것이다. 한글 이름을 쓰면 DB locale에 따라
            # 순서가 파이썬 sorted()와 갈릴 수 있다.
            ExampleCategory(name="alpha"),
            ExampleCategory(name="beta"),
            ExampleCategory(name="gamma"),
            ExampleTag(name="draft-only"),
            ExampleTag(name="public"),
        ]
    )
    session.commit()


def test_categories_collection_is_sorted_by_name(
    client: TestClient,
    committed_session: Session,
) -> None:
    _persist_reference_data(committed_session)

    response = client.get("/api/v1/categories", headers=HEADERS)

    assert response.status_code == 200
    document = response.json()
    names = [resource["attributes"]["name"] for resource in document["data"]]
    assert names == sorted(names)
    assert all(resource["type"] == "exampleCategories" for resource in document["data"])


def test_category_single_resource_carries_self_link(
    client: TestClient,
    committed_session: Session,
) -> None:
    _persist_reference_data(committed_session)
    category_id = client.get("/api/v1/categories", headers=HEADERS).json()["data"][0]["id"]

    response = client.get(f"/api/v1/categories/{category_id}", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()["data"]["links"]["self"] == f"/api/v1/categories/{category_id}"


def test_categories_support_the_declared_name_filters(
    client: TestClient,
    committed_session: Session,
) -> None:
    _persist_reference_data(committed_session)

    exact = client.get("/api/v1/categories?filter[name]=beta", headers=HEADERS)
    contains = client.get("/api/v1/categories?filter[name][contains]=et", headers=HEADERS)

    assert exact.status_code == 200
    assert [resource["attributes"]["name"] for resource in exact.json()["data"]] == ["beta"]
    assert contains.status_code == 200
    assert [resource["attributes"]["name"] for resource in contains.json()["data"]] == ["beta"]


def test_categories_walk_the_whole_collection_by_cursor(
    client: TestClient,
    committed_session: Session,
) -> None:
    _persist_reference_data(committed_session)

    seen: list[str] = []
    url = "/api/v1/categories?page[size]=2&page[after]="
    while url is not None:
        document = client.get(url, headers=HEADERS).json()
        seen.extend(resource["attributes"]["name"] for resource in document["data"])
        url = document["links"].get("next")

    assert seen == ["alpha", "beta", "gamma"]


def test_categories_reject_an_undeclared_filter_operator(client: TestClient) -> None:
    response = client.get("/api/v1/categories?filter[name][gt]=a", headers=HEADERS)

    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "INVALID_FILTER"


def test_categories_reject_an_undeclared_include(client: TestClient) -> None:
    response = client.get("/api/v1/categories?include=examples", headers=HEADERS)

    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "INVALID_INCLUDE"


def test_categories_do_not_accept_writes(client: TestClient) -> None:
    response = client.post(
        "/api/v1/categories",
        headers={**HEADERS, "Content-Type": JSONAPI_MEDIA_TYPE},
        content=b'{"data":{"type":"exampleCategories","attributes":{"name":"delta"}}}',
    )

    assert response.status_code == 405
    assert response.json()["errors"][0]["code"] == "HTTP_ERROR"


def test_tags_collection_is_sorted_by_name(
    client: TestClient,
    committed_session: Session,
) -> None:
    _persist_reference_data(committed_session)

    response = client.get("/api/v1/tags", headers=HEADERS)

    assert response.status_code == 200
    document = response.json()
    names = [resource["attributes"]["name"] for resource in document["data"]]
    assert names == sorted(names)
    assert all(resource["type"] == "exampleTags" for resource in document["data"])


def test_tags_do_not_accept_writes(client: TestClient) -> None:
    response = client.delete(
        "/api/v1/tags/00000000-0000-0000-0000-000000000000",
        headers=HEADERS,
    )

    assert response.status_code == 405
    assert response.json()["errors"][0]["code"] == "HTTP_ERROR"
```

- [ ] **Step 2: 테스트가 통과하는 것을 확인한다**

Run: `uv run poe db-up && uv run pytest tests/integration/test_reference_resources.py -v`

Expected: PASS.

> 이 작업은 Task 3이 이미 만든 라우트를 **검증**하는 것이라 red 단계가 없는 것이 정상이다. 실패가 나오면 그것은 이 테스트의 red가 아니라 Task 3의 버그다. 그 경우 Task 3으로 돌아가 고친다.

- [ ] **Step 3: README에 새 라우트를 문서화한다**

`README.md`의 Example 관계를 설명하는 절 뒤에 추가한다.

```markdown
## 참조 자원

분류와 라벨은 읽기 전용 컬렉션으로도 조회할 수 있습니다. 관계 선택기처럼
고를 목록이 필요한 화면을 위한 것이며, 쓰기 라우트는 없습니다.

```text
GET /api/v1/categories       filter[name] · sort=name,createdAt · page[...]
GET /api/v1/categories/{id}
GET /api/v1/tags
GET /api/v1/tags/{id}
```

기본 정렬은 `name` 오름차순입니다. JSON:API 자원 타입은 각각
`exampleCategories`와 `exampleTags`로, URL 경로와 다릅니다.

```bash
curl --globoff -fsS \
  -H 'Accept: application/vnd.api+json' \
  'http://localhost:4000/api/v1/categories?filter[name][contains]=문서'
```
```

- [ ] **Step 4: `app/AGENTS.md`에 읽기 전용 옵션을 적는다**

`app/AGENTS.md`의 컨트롤러 선언 규칙을 다루는 절에 한 문단을 더한다.

```markdown
- 참조 데이터처럼 서버가 관리하는 자원은 `enable_writes = False`를 선언한다.
  쓰기 라우트와 관계 mutation 라우트가 등록되지 않고, `create_schema`·
  `update_schema`·`replace_schema`를 선언하지 않아도 된다. 제네릭 인자는
  `CrudActions[Model, BaseModel, BaseModel, BaseModel]`로 묶는다.
```

- [ ] **Step 5: 전체 게이트를 돌린다**

Run: `uv run poe check`

Expected: 전부 통과. 이 스크립트가 일회용 PostgreSQL을 띄우므로 bash(Git Bash 또는 WSL)가 필요하다.

- [ ] **Step 6: 커밋**

```bash
git add tests/integration/test_reference_resources.py README.md app/AGENTS.md
git commit -m "test: cover reference resource routes end to end

실제 PostgreSQL로 목록 정렬·단건 self 링크·name 필터·미선언 연산자 거부·
미선언 include 거부·쓰기 405를 확인한다.

README에 새 라우트를, app/AGENTS.md에 enable_writes 선언 규칙을 적는다."
```

---

## 완료 조건

`uv run poe check`가 통과하고, 아래가 모두 참이다.

- `GET /api/v1/categories`와 `GET /api/v1/tags`가 `name` 오름차순 목록을 낸다.
- 두 자원의 `type`이 `exampleCategories`, `exampleTags`다.
- 두 경로의 쓰기 메서드가 405를 낸다.
- `GET /api/v1/examples?include=category,tags`의 `included[]`에 `links.self`가 있다.
- `ExamplesController`의 쓰기 라우트가 그대로 남아 있다.

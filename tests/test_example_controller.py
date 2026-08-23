"""Production Example JSON:API controller integration tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import uuid4

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session
from starlette.routing import Route

from app.auth.tokens import create_token
from app.controllers.concerns.jsonapi_routes import JsonApiRoute
from app.jsonapi import JSONAPI_MEDIA_TYPE, require_jsonapi_accept
from app.models import Example, ExampleCategory, ExampleStatus, ExampleTag, User
from app.serializers import ExampleSerializer
from config.auth import AuthSettings


def _persist_example(session: Session) -> Example:
    example = Example(
        title="통합 예시",
        description=None,
        status=ExampleStatus.ACTIVE,
        score=90,
    )
    ExampleSerializer.initialize_relationship_defaults(example)
    session.add(example)
    session.commit()
    return example


def _create_document(*, title: str = "보호된 생성") -> dict[str, object]:
    return {
        "data": {
            "type": "examples",
            "attributes": {
                "title": title,
                "description": None,
                "status": "active",
                "score": 80,
            },
        }
    }


def test_openapi_exposes_only_declared_application_operations(app: FastAPI) -> None:
    schema = app.openapi()

    assert {path: set(item) for path, item in schema["paths"].items()} == {
        "/api/v1/auth/login": {"post"},
        "/api/v1/auth/logout": {"post"},
        "/api/v1/auth/refresh": {"post"},
        "/api/v1/auth/register": {"post"},
        "/api/v1/users/me": {"get"},
        "/api/v1/examples": {"get", "post"},
        "/api/v1/examples/{resource_id}": {"get", "patch", "put", "delete"},
        "/api/v1/examples/{resource_id}/relationships/category": {"get", "patch"},
        "/api/v1/examples/{resource_id}/category": {"get"},
        "/api/v1/examples/{resource_id}/relationships/tags": {
            "get",
            "post",
            "patch",
            "delete",
        },
        "/api/v1/examples/{resource_id}/tags": {"get"},
        "/health/live": {"get"},
        "/health/ready": {"get"},
    }
    assert schema["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }


def test_openapi_operation_ids_and_route_names_are_stable(app: FastAPI) -> None:
    """Pin the published operation ids and the ``name=`` strings they are derived from.

    ``CrudActions`` builds every example route through ``route_registrar``; the route
    names are the only input FastAPI uses for ``operationId``, so moving the registration
    between modules must not touch one character of them.
    """

    schema = app.openapi()
    operation_ids = {
        (path, method): operation["operationId"]
        for path, item in schema["paths"].items()
        if path.startswith("/api/v1/examples")
        for method, operation in item.items()
    }

    assert operation_ids == {
        ("/api/v1/examples", "get"): "ExamplesController_index_api_v1_examples_get",
        ("/api/v1/examples", "post"): "ExamplesController_create_api_v1_examples_post",
        (
            "/api/v1/examples/{resource_id}",
            "get",
        ): "ExamplesController_show_api_v1_examples__resource_id__get",
        (
            "/api/v1/examples/{resource_id}",
            "patch",
        ): "ExamplesController_update_api_v1_examples__resource_id__patch",
        (
            "/api/v1/examples/{resource_id}",
            "put",
        ): "ExamplesController_upsert_api_v1_examples__resource_id__put",
        (
            "/api/v1/examples/{resource_id}",
            "delete",
        ): "ExamplesController_destroy_api_v1_examples__resource_id__delete",
        ("/api/v1/examples/{resource_id}/relationships/category", "get"): (
            "ExamplesController_relationship_category_show_api_v1_examples__resource_id__relationships_category_get"
        ),
        ("/api/v1/examples/{resource_id}/relationships/category", "patch"): (
            "ExamplesController_relationship_category_replace"
            "_api_v1_examples__resource_id__relationships_category_patch"
        ),
        ("/api/v1/examples/{resource_id}/category", "get"): (
            "ExamplesController_relationship_category_related_api_v1_examples__resource_id__category_get"
        ),
        ("/api/v1/examples/{resource_id}/relationships/tags", "get"): (
            "ExamplesController_relationship_tags_show_api_v1_examples__resource_id__relationships_tags_get"
        ),
        ("/api/v1/examples/{resource_id}/relationships/tags", "post"): (
            "ExamplesController_relationship_tags_add_api_v1_examples__resource_id__relationships_tags_post"
        ),
        ("/api/v1/examples/{resource_id}/relationships/tags", "patch"): (
            "ExamplesController_relationship_tags_replace_api_v1_examples__resource_id__relationships_tags_patch"
        ),
        ("/api/v1/examples/{resource_id}/relationships/tags", "delete"): (
            "ExamplesController_relationship_tags_remove_api_v1_examples__resource_id__relationships_tags_delete"
        ),
        ("/api/v1/examples/{resource_id}/tags", "get"): (
            "ExamplesController_relationship_tags_related_api_v1_examples__resource_id__tags_get"
        ),
    }
    assert {
        route.name for route in app.routes if isinstance(route, APIRoute) and route.path.startswith("/api/v1/examples")
    } == {
        "ExamplesController.index",
        "ExamplesController.create",
        "ExamplesController.show",
        "ExamplesController.update",
        "ExamplesController.upsert",
        "ExamplesController.destroy",
        "ExamplesController.relationship.category.show",
        "ExamplesController.relationship.category.related",
        "ExamplesController.relationship.category.replace",
        "ExamplesController.relationship.tags.show",
        "ExamplesController.relationship.tags.related",
        "ExamplesController.relationship.tags.add",
        "ExamplesController.relationship.tags.replace",
        "ExamplesController.relationship.tags.remove",
    }


def test_openapi_component_schema_names_are_stable(app: FastAPI) -> None:
    """Pin the published component names for the example write schemas.

    A PEP 695 ``type`` alias used as a field annotation becomes the component key
    in ``components.schemas``, so wrapping ``ExampleStatus`` in an alias silently
    renames the published schema and breaks every generated client.
    """

    schema = app.openapi()
    components = schema["components"]["schemas"]

    assert {"ExampleStatus", "ExampleCreate", "ExampleUpdate", "ExampleReplace"} <= set(components)
    assert components["ExampleStatus"]["enum"] == ["draft", "active", "archived"]
    for name in ("ExampleCreate", "ExampleUpdate", "ExampleReplace"):
        assert components[name]["properties"]["status"] == {"$ref": "#/components/schemas/ExampleStatus"}


def test_example_openapi_declares_exact_error_response_sets(app: FastAPI) -> None:
    """Pin every declared status code per operation, not just a superset."""

    schema = app.openapi()
    declared = {
        (path, method): sorted(operation["responses"])
        for path, item in schema["paths"].items()
        if path.startswith("/api/v1/examples")
        for method, operation in item.items()
    }

    assert declared == {
        ("/api/v1/examples", "get"): ["200", "400", "406", "422", "500"],
        ("/api/v1/examples", "post"): ["201", "400", "401", "403", "406", "409", "415", "422", "500"],
        ("/api/v1/examples/{resource_id}", "get"): ["200", "400", "404", "406", "422", "500"],
        ("/api/v1/examples/{resource_id}", "patch"): [
            "200",
            "400",
            "401",
            "403",
            "404",
            "406",
            "409",
            "415",
            "422",
            "500",
        ],
        ("/api/v1/examples/{resource_id}", "put"): [
            "200",
            "201",
            "400",
            "401",
            "403",
            "404",
            "406",
            "409",
            "415",
            "422",
            "500",
        ],
        ("/api/v1/examples/{resource_id}", "delete"): [
            "204",
            "400",
            "401",
            "403",
            "404",
            "406",
            "422",
            "500",
        ],
        ("/api/v1/examples/{resource_id}/relationships/category", "get"): [
            "200",
            "400",
            "404",
            "406",
            "422",
            "500",
        ],
        ("/api/v1/examples/{resource_id}/relationships/category", "patch"): [
            "204",
            "400",
            "401",
            "403",
            "404",
            "406",
            "409",
            "415",
            "422",
            "500",
        ],
        ("/api/v1/examples/{resource_id}/category", "get"): ["200", "400", "404", "406", "422", "500"],
        ("/api/v1/examples/{resource_id}/relationships/tags", "get"): ["200", "400", "404", "406", "422", "500"],
        ("/api/v1/examples/{resource_id}/relationships/tags", "post"): [
            "204",
            "400",
            "401",
            "403",
            "404",
            "406",
            "409",
            "415",
            "422",
            "500",
        ],
        ("/api/v1/examples/{resource_id}/relationships/tags", "patch"): [
            "204",
            "400",
            "401",
            "403",
            "404",
            "406",
            "409",
            "415",
            "422",
            "500",
        ],
        ("/api/v1/examples/{resource_id}/relationships/tags", "delete"): [
            "204",
            "400",
            "401",
            "403",
            "404",
            "406",
            "409",
            "415",
            "422",
            "500",
        ],
        ("/api/v1/examples/{resource_id}/tags", "get"): ["200", "400", "404", "406", "422", "500"],
    }
    created = schema["paths"]["/api/v1/examples/{resource_id}"]["put"]["responses"]["201"]
    assert created["headers"] == {
        "Location": {
            "description": "Canonical URL of the created resource",
            "schema": {"type": "string"},
        }
    }


def test_index_returns_jsonapi_collection(
    client: TestClient,
    committed_session: Session,
) -> None:
    example = _persist_example(committed_session)

    response = client.get(
        "/api/v1/examples",
        headers={"Accept": JSONAPI_MEDIA_TYPE},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert [resource["id"] for resource in response.json()["data"]] == [str(example.id)]
    assert response.json()["data"][0]["type"] == "examples"
    assert response.json()["data"][0]["attributes"]["title"] == "통합 예시"


def test_application_exposes_only_explicitly_composed_routes(app: FastAPI) -> None:
    assert {route.path for route in app.routes if isinstance(route, Route)} == {
        "/api/schema",
        "/api-docs",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/refresh",
        "/api/v1/auth/register",
        "/api/v1/users/me",
        "/api/v1/examples",
        "/api/v1/examples/{resource_id}",
        "/api/v1/examples/{resource_id}/relationships/category",
        "/api/v1/examples/{resource_id}/category",
        "/api/v1/examples/{resource_id}/relationships/tags",
        "/api/v1/examples/{resource_id}/tags",
        "/health/live",
        "/health/ready",
    }


def test_every_application_route_uses_the_shared_jsonapi_assembly(app: FastAPI) -> None:
    """Every route must inherit the base's Content-Type and Accept enforcement.

    Only the health probes may skip Accept negotiation, and they say so with
    ``HealthController.negotiate_accept = False``.
    """

    accept_optout_paths = {"/health/live", "/health/ready"}
    api_routes = [route for route in app.routes if isinstance(route, APIRoute)]

    assert api_routes
    assert [route.path for route in api_routes if not isinstance(route, JsonApiRoute)] == []
    assert {
        route.path
        for route in api_routes
        if require_jsonapi_accept not in [dependency.dependency for dependency in route.dependencies]
    } == accept_optout_paths


def test_legacy_example_route_is_removed(client: TestClient) -> None:
    response = client.get("/api/v1/example")

    assert response.status_code == 404
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json()["errors"][0]["code"] == "RESOURCE_NOT_FOUND"


def test_all_example_read_routes_remain_public(
    client: TestClient,
    committed_session: Session,
) -> None:
    category = ExampleCategory(name="공개 카테고리")
    tag = ExampleTag(name="공개 태그")
    example = Example(
        title="공개 조회",
        description=None,
        status=ExampleStatus.ACTIVE,
        score=90,
        category=category,
        tags=[tag],
    )
    committed_session.add(example)
    committed_session.commit()

    paths = (
        "/api/v1/examples",
        f"/api/v1/examples/{example.id}",
        f"/api/v1/examples/{example.id}/relationships/category",
        f"/api/v1/examples/{example.id}/category",
        f"/api/v1/examples/{example.id}/relationships/tags",
        f"/api/v1/examples/{example.id}/tags",
    )

    for path in paths:
        response = client.get(path, headers={"Accept": JSONAPI_MEDIA_TYPE})
        assert response.status_code == 200, (path, response.text)


def test_all_example_write_routes_require_bearer_authentication(
    client: TestClient,
    committed_session: Session,
) -> None:
    category = ExampleCategory(name="보호 카테고리")
    tag = ExampleTag(name="보호 태그")
    example = _persist_example(committed_session)
    committed_session.add_all([category, tag])
    committed_session.commit()
    resource_id = str(example.id)
    created_id = str(uuid4())
    headers = {"Accept": JSONAPI_MEDIA_TYPE, "Content-Type": JSONAPI_MEDIA_TYPE}
    requests = (
        ("POST", "/api/v1/examples", _create_document()),
        (
            "PATCH",
            f"/api/v1/examples/{resource_id}",
            {"data": {"type": "examples", "id": resource_id, "attributes": {"title": "변경"}}},
        ),
        (
            "PUT",
            f"/api/v1/examples/{created_id}",
            {
                "data": {
                    "type": "examples",
                    "id": created_id,
                    "attributes": _create_document()["data"]["attributes"],  # type: ignore[index]
                }
            },
        ),
        ("DELETE", f"/api/v1/examples/{resource_id}", None),
        (
            "PATCH",
            f"/api/v1/examples/{resource_id}/relationships/category",
            {"data": {"type": "exampleCategories", "id": str(category.id)}},
        ),
        (
            "POST",
            f"/api/v1/examples/{resource_id}/relationships/tags",
            {"data": [{"type": "exampleTags", "id": str(tag.id)}]},
        ),
        (
            "PATCH",
            f"/api/v1/examples/{resource_id}/relationships/tags",
            {"data": [{"type": "exampleTags", "id": str(tag.id)}]},
        ),
        (
            "DELETE",
            f"/api/v1/examples/{resource_id}/relationships/tags",
            {"data": [{"type": "exampleTags", "id": str(tag.id)}]},
        ),
    )

    for method, path, document in requests:
        response = client.request(method, path, headers=headers, json=document)
        assert response.status_code == 401, (method, path, response.text)
        assert response.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"


def test_active_bearer_can_create_example(
    authenticated_client: TestClient,
    jsonapi_headers: dict[str, str],
) -> None:
    response = authenticated_client.post(
        "/api/v1/examples",
        headers=jsonapi_headers,
        json=_create_document(),
    )

    assert response.status_code == 201
    assert response.json()["data"]["attributes"]["title"] == "보호된 생성"


def test_create_accepts_the_status_enum_as_a_plain_json_string(
    authenticated_client: TestClient,
    jsonapi_headers: dict[str, str],
) -> None:
    """Model-level ``strict=True`` alone would turn every example write into a 422."""

    response = authenticated_client.post(
        "/api/v1/examples",
        headers=jsonapi_headers,
        json=_create_document(),
    )

    assert response.status_code == 201, response.text
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    assert response.json()["data"]["attributes"]["status"] == "active"


def test_create_rejects_a_coercible_score_string_with_a_jsonapi_pointer(
    authenticated_client: TestClient,
    jsonapi_headers: dict[str, str],
) -> None:
    document = _create_document()
    attributes = document["data"]["attributes"]  # type: ignore[index]
    attributes["score"] = "50"

    response = authenticated_client.post(
        "/api/v1/examples",
        headers=jsonapi_headers,
        json=document,
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    error = response.json()["errors"][0]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["source"]["pointer"] == "/data/attributes/score"


def test_example_openapi_uses_the_shared_error_response_descriptions(app: FastAPI) -> None:
    responses = app.openapi()["paths"]["/api/v1/examples"]["post"]["responses"]

    assert responses["401"]["description"] == "Authentication required"
    assert responses["403"]["description"] == "Forbidden"
    assert responses["422"]["description"] == "Validation error"
    assert app.openapi()["paths"]["/health/ready"]["get"]["responses"]["503"]["description"] == ("Service unavailable")


def test_inactive_bearer_cannot_write_examples(
    client: TestClient,
    committed_session: Session,
    jsonapi_headers: dict[str, str],
    persisted_user: Callable[..., User],
    access_token: Callable[[FastAPI, User], str],
    app: FastAPI,
) -> None:
    user = persisted_user(committed_session, is_active=False)

    response = client.post(
        "/api/v1/examples",
        headers={**jsonapi_headers, "Authorization": f"Bearer {access_token(app, user)}"},
        json=_create_document(),
    )

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "USER_INACTIVE"


def test_example_openapi_protects_only_write_operations(app: FastAPI) -> None:
    schema = app.openapi()
    paths = schema["paths"]
    protected = (
        ("/api/v1/examples", "post"),
        ("/api/v1/examples/{resource_id}", "patch"),
        ("/api/v1/examples/{resource_id}", "put"),
        ("/api/v1/examples/{resource_id}", "delete"),
        ("/api/v1/examples/{resource_id}/relationships/category", "patch"),
        ("/api/v1/examples/{resource_id}/relationships/tags", "post"),
        ("/api/v1/examples/{resource_id}/relationships/tags", "patch"),
        ("/api/v1/examples/{resource_id}/relationships/tags", "delete"),
    )
    public = (
        ("/api/v1/examples", "get"),
        ("/api/v1/examples/{resource_id}", "get"),
        ("/api/v1/examples/{resource_id}/relationships/category", "get"),
        ("/api/v1/examples/{resource_id}/category", "get"),
        ("/api/v1/examples/{resource_id}/relationships/tags", "get"),
        ("/api/v1/examples/{resource_id}/tags", "get"),
    )

    for path, method in protected:
        operation = paths[path][method]
        assert operation["security"] == [{"BearerAuth": []}]
        assert set(operation["responses"]) >= {"401", "403"}
    for path, method in public:
        assert "security" not in paths[path][method]


def _instrumented_application(
    app_factory: Callable[..., FastAPI],
    db_engine: Engine,
    *,
    auth_sessions: list[Session] | None = None,
    crud_sessions: list[Session] | None = None,
    auth_state_at_crud_open: list[bool] | None = None,
) -> FastAPI:
    """Build the shared application with recording session overrides.

    The assembly still lives in ``conftest.app_factory``; only the two recording
    generators are local, because these tests exist to observe them.

    ``auth_state_at_crud_open`` samples every recorded auth session's
    ``in_transaction()`` at the instant the CRUD session is opened. That instant is
    the only place the ordering is observable: after the request every generator
    dependency has already been torn down, so a post-request check cannot tell a
    lookup session that closed before the endpoint from one that closed after it.
    """

    def override_auth_session_factory() -> Callable[[], Session]:
        def build_session() -> Session:
            session = Session(bind=db_engine, expire_on_commit=False)
            if auth_sessions is not None:
                auth_sessions.append(session)
            return session

        return build_session

    def override_crud_session() -> Iterator[Session]:
        if auth_state_at_crud_open is not None and auth_sessions is not None:
            auth_state_at_crud_open.extend(opened.in_transaction() for opened in auth_sessions)
        with Session(bind=db_engine, expire_on_commit=False) as session:
            if crud_sessions is not None:
                crud_sessions.append(session)
            yield session

    return app_factory(
        session_override=override_crud_session,
        auth_session_factory_override=override_auth_session_factory,
    )


@contextmanager
def _recorded_pool_events(db_engine: Engine) -> Iterator[list[str]]:
    """Record pool checkout/checkin events for the duration of the block."""

    events: list[str] = []

    def on_checkout(*_: object) -> None:
        events.append("checkout")

    def on_checkin(*_: object) -> None:
        events.append("checkin")

    event.listen(db_engine, "checkout", on_checkout)
    event.listen(db_engine, "checkin", on_checkin)
    try:
        yield events
    finally:
        event.remove(db_engine, "checkout", on_checkout)
        event.remove(db_engine, "checkin", on_checkin)


def _peak_concurrent_checkouts(events: list[str]) -> int:
    depth = 0
    peak = 0
    for name in events:
        depth += 1 if name == "checkout" else -1
        peak = max(peak, depth)
    return peak


def test_auth_session_is_closed_before_the_crud_transaction_begins(
    app_factory: Callable[..., FastAPI],
    db_engine: Engine,
    committed_session: Session,
    persisted_user: Callable[..., User],
    access_token: Callable[[FastAPI, User], str],
) -> None:
    user = persisted_user(committed_session)
    auth_sessions: list[Session] = []
    crud_sessions: list[Session] = []
    auth_state_at_crud_open: list[bool] = []
    application = _instrumented_application(
        app_factory,
        db_engine,
        auth_sessions=auth_sessions,
        crud_sessions=crud_sessions,
        auth_state_at_crud_open=auth_state_at_crud_open,
    )
    token = access_token(application, user)

    with TestClient(application, raise_server_exceptions=False) as transaction_client:
        response = transaction_client.post(
            "/api/v1/examples",
            headers={
                "Accept": JSONAPI_MEDIA_TYPE,
                "Content-Type": JSONAPI_MEDIA_TYPE,
                "Authorization": f"Bearer {token}",
            },
            json=_create_document(title="독립 세션 생성"),
        )

    assert response.status_code == 201
    assert len(auth_sessions) == 1
    assert len(crud_sessions) == 1
    assert auth_sessions[0] is not crud_sessions[0]
    assert auth_sessions[0].in_transaction() is False
    # Sampled while the CRUD session was being opened, so this is the ordering claim in
    # the name: the bearer lookup session was already closed by then, not merely by the
    # end of the request.
    assert auth_state_at_crud_open == [False]


def test_authenticated_write_holds_at_most_one_pool_connection(
    app_factory: Callable[..., FastAPI],
    db_engine: Engine,
    committed_session: Session,
    persisted_user: Callable[..., User],
    access_token: Callable[[FastAPI, User], str],
) -> None:
    user = persisted_user(committed_session)
    application = _instrumented_application(app_factory, db_engine)
    token = access_token(application, user)

    with (
        TestClient(application, raise_server_exceptions=False) as budget_client,
        _recorded_pool_events(db_engine) as events,
    ):
        response = budget_client.post(
            "/api/v1/examples",
            headers={
                "Accept": JSONAPI_MEDIA_TYPE,
                "Content-Type": JSONAPI_MEDIA_TYPE,
                "Authorization": f"Bearer {token}",
            },
            json=_create_document(title="커넥션 예산"),
        )

    assert response.status_code == 201
    assert events.count("checkout") == 2
    assert _peak_concurrent_checkouts(events) == 1
    assert events == ["checkout", "checkin", "checkout", "checkin"]


def test_public_read_holds_at_most_one_pool_connection(
    app_factory: Callable[..., FastAPI],
    db_engine: Engine,
    committed_session: Session,
) -> None:
    _persist_example(committed_session)
    application = _instrumented_application(app_factory, db_engine)

    with (
        TestClient(application, raise_server_exceptions=False) as budget_client,
        _recorded_pool_events(db_engine) as events,
    ):
        response = budget_client.get(
            "/api/v1/examples",
            headers={"Accept": JSONAPI_MEDIA_TYPE},
        )

    assert response.status_code == 200
    assert _peak_concurrent_checkouts(events) == 1


def test_failed_auth_lookup_leaves_the_request_transaction_clean(
    app_factory: Callable[..., FastAPI],
    db_engine: Engine,
    committed_session: Session,
) -> None:
    crud_sessions: list[Session] = []
    application = _instrumented_application(app_factory, db_engine, crud_sessions=crud_sessions)
    settings = application.state.auth_settings
    assert isinstance(settings, AuthSettings)
    token = create_token(uuid4(), token_type="access", settings=settings)

    with TestClient(application, raise_server_exceptions=False) as failing_client:
        response = failing_client.post(
            "/api/v1/examples",
            headers={
                "Accept": JSONAPI_MEDIA_TYPE,
                "Content-Type": JSONAPI_MEDIA_TYPE,
                "Authorization": f"Bearer {token}",
            },
            json=_create_document(title="오염되지 않은 트랜잭션"),
        )

    assert response.status_code == 401
    error = response.json()["errors"][0]
    assert error["code"] == "INVALID_TOKEN"
    assert error["source"]["header"] == "Authorization"
    assert all(session.in_transaction() is False for session in crud_sessions)
    assert committed_session.scalars(select(Example)).all() == []


@contextmanager
def _recorded_statements(db_engine: Engine) -> Iterator[list[str]]:
    """Record every statement the engine executes for the duration of the block."""

    statements: list[str] = []

    def record(_connection: object, _cursor: object, statement: str, *_: object) -> None:
        statements.append(statement)

    event.listen(db_engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(db_engine, "before_cursor_execute", record)


def _persist_linkage_fixture(session: Session) -> tuple[ExampleCategory, ExampleTag, Example]:
    """Three examples sharing one category and one tag, plus one without a category."""

    category = ExampleCategory(name="연결 카테고리")
    tag = ExampleTag(name="연결 태그")
    for index in range(3):
        session.add(
            Example(
                title=f"연결 {index}",
                description=None,
                status=ExampleStatus.ACTIVE,
                score=10 + index,
                category=category,
                tags=[tag],
            )
        )
    uncategorized = Example(
        title="카테고리 없음",
        description=None,
        status=ExampleStatus.ACTIVE,
        score=1,
        category=None,
        tags=[],
    )
    session.add(uncategorized)
    session.commit()
    return category, tag, uncategorized


def test_index_without_include_runs_no_category_query_and_reads_only_tag_ids(
    client: TestClient,
    committed_session: Session,
    db_engine: Engine,
) -> None:
    _persist_linkage_fixture(committed_session)

    with _recorded_statements(db_engine) as statements:
        response = client.get("/api/v1/examples", headers={"Accept": JSONAPI_MEDIA_TYPE})

    assert response.status_code == 200
    assert response.headers["content-type"] == JSONAPI_MEDIA_TYPE
    # The page itself plus the tag linkage load. The COUNT that used to run here is now
    # opt-in through ``page[totals]=true``.
    assert len(statements) == 2
    assert all("count(" not in statement.lower() for statement in statements)
    assert all(" FROM categories" not in statement for statement in statements)
    tag_statements = [statement for statement in statements if "example_tags" in statement]
    assert len(tag_statements) == 1
    assert "tags.id" in tag_statements[0]
    assert "tags.name" not in tag_statements[0]
    assert "tags.created_at" not in tag_statements[0]


def test_index_without_include_still_emits_full_relationship_linkage(
    client: TestClient,
    committed_session: Session,
) -> None:
    category, tag, uncategorized = _persist_linkage_fixture(committed_session)

    response = client.get("/api/v1/examples", headers={"Accept": JSONAPI_MEDIA_TYPE})

    assert response.status_code == 200
    body = response.json()
    assert "included" not in body
    resources = {resource["id"]: resource for resource in body["data"]}
    assert len(resources) == 4
    categorized = [resource for resource in body["data"] if resource["id"] != str(uncategorized.id)]
    assert len(categorized) == 3
    for resource in categorized:
        relationships = resource["relationships"]
        assert relationships["category"]["data"] == {
            "type": "exampleCategories",
            "id": str(category.id),
        }
        assert relationships["tags"]["data"] == [{"type": "exampleTags", "id": str(tag.id)}]
        assert relationships["category"]["links"] == {
            "self": f"/api/v1/examples/{resource['id']}/relationships/category",
            "related": f"/api/v1/examples/{resource['id']}/category",
        }
        assert relationships["tags"]["links"] == {
            "self": f"/api/v1/examples/{resource['id']}/relationships/tags",
            "related": f"/api/v1/examples/{resource['id']}/tags",
        }
    empty = resources[str(uncategorized.id)]["relationships"]
    assert empty["category"]["data"] is None
    assert empty["tags"]["data"] == []


def test_include_category_and_tags_document_is_unchanged(
    client: TestClient,
    committed_session: Session,
) -> None:
    category, tag, _uncategorized = _persist_linkage_fixture(committed_session)
    baseline = client.get("/api/v1/examples", headers={"Accept": JSONAPI_MEDIA_TYPE}).json()

    both = client.get(
        "/api/v1/examples?include=category,tags",
        headers={"Accept": JSONAPI_MEDIA_TYPE},
    )
    only_category = client.get(
        "/api/v1/examples?include=category",
        headers={"Accept": JSONAPI_MEDIA_TYPE},
    )
    empty_include = client.get(
        "/api/v1/examples?include=",
        headers={"Accept": JSONAPI_MEDIA_TYPE},
    )

    assert both.status_code == 200
    assert both.json()["data"] == baseline["data"]
    assert both.json()["included"] == [
        {
            "type": "exampleCategories",
            "id": str(category.id),
            "attributes": {"name": category.name},
        },
        {
            "type": "exampleTags",
            "id": str(tag.id),
            "attributes": {"name": tag.name},
        },
    ]

    assert only_category.status_code == 200
    assert only_category.json()["data"] == baseline["data"]
    assert only_category.json()["included"] == [
        {
            "type": "exampleCategories",
            "id": str(category.id),
            "attributes": {"name": category.name},
        }
    ]

    assert empty_include.status_code == 200
    assert empty_include.json()["data"] == baseline["data"]
    assert empty_include.json()["included"] == []


def test_show_without_include_avoids_the_category_query(
    client: TestClient,
    committed_session: Session,
    db_engine: Engine,
) -> None:
    _persist_linkage_fixture(committed_session)
    index_body = client.get("/api/v1/examples", headers={"Accept": JSONAPI_MEDIA_TYPE}).json()
    expected = index_body["data"][0]

    with _recorded_statements(db_engine) as statements:
        response = client.get(
            f"/api/v1/examples/{expected['id']}",
            headers={"Accept": JSONAPI_MEDIA_TYPE},
        )

    assert response.status_code == 200
    assert response.json()["data"] == expected
    assert len(statements) == 2
    assert all(" FROM categories" not in statement for statement in statements)


def test_repeated_invalid_include_is_rejected_after_a_cached_success(
    client: TestClient,
    committed_session: Session,
) -> None:
    _persist_linkage_fixture(committed_session)

    assert (
        client.get(
            "/api/v1/examples?include=category",
            headers={"Accept": JSONAPI_MEDIA_TYPE},
        ).status_code
        == 200
    )

    for query in ("include=missing", "include=missing", "include=category,,tags"):
        rejected = client.get(f"/api/v1/examples?{query}", headers={"Accept": JSONAPI_MEDIA_TYPE})

        assert rejected.status_code == 400
        assert rejected.headers["content-type"] == JSONAPI_MEDIA_TYPE
        error = rejected.json()["errors"][0]
        assert error["code"] == "INVALID_INCLUDE"
        assert error["source"]["parameter"] == "include"

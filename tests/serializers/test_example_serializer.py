"""Declarative JSON:API serializer contracts for the example domain."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import inf, nan
from typing import Any, ClassVar
from uuid import UUID

import pytest
from sqlalchemy import Engine, ForeignKey, Integer, String, and_, event, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    defer,
    mapped_column,
    relationship,
    selectinload,
)

from app.models import Example, ExampleCategory, ExampleStatus, ExampleTag
from app.serializers import (
    ExampleSerializer,
    JsonApiSerializationError,
    JsonApiSerializer,
    RelationshipDefinition,
    build_include_tree,
)


@dataclass
class _Child:
    id: UUID


class _ChildSerializer(JsonApiSerializer[_Child]):
    type_name = "children"
    resource_path = "/children"
    attributes = ()


@dataclass
class _Parent:
    id: UUID
    child: object


class _ManyParentSerializer(JsonApiSerializer[_Parent]):
    type_name = "parents"
    resource_path = "/parents"
    attributes = ()
    relationships: ClassVar[dict[str, RelationshipDefinition]] = {
        "children": RelationshipDefinition(attribute="child", serializer=_ChildSerializer, many=True)
    }


class _OneParentSerializer(JsonApiSerializer[_Parent]):
    type_name = "parents"
    resource_path = "/parents"
    attributes = ()
    relationships: ClassVar[dict[str, RelationshipDefinition]] = {
        "child": RelationshipDefinition(attribute="child", serializer=_ChildSerializer, many=False)
    }


class _WireStatus(StrEnum):
    READY = "ready"


@dataclass
class _Encoded:
    id: UUID
    api_url: str
    payload: object


class _EncodedSerializer(JsonApiSerializer[_Encoded]):
    type_name = "encoded"
    resource_path = "/encoded"
    attributes = ("api_url", "payload")


@dataclass
class _GraphNode:
    id: UUID
    name: str
    left: _GraphNode | None = None
    right: _GraphNode | None = None


class _GraphSerializer(JsonApiSerializer[_GraphNode]):
    type_name = "graphNodes"
    resource_path = None
    attributes = ("name",)


_GraphSerializer.relationships = {
    "left": RelationshipDefinition(attribute="left", serializer=_GraphSerializer, many=False),
    "right": RelationshipDefinition(attribute="right", serializer=_GraphSerializer, many=False),
}


@pytest.fixture
def example() -> Example:
    category = ExampleCategory(id=UUID(int=10), name="examples")
    tag = ExampleTag(id=UUID(int=20), name="filing")
    return Example(
        id=UUID(int=1),
        title="Example item",
        description=None,
        status=ExampleStatus.ACTIVE,
        score=80,
        category=category,
        tags=[tag],
        created_at=datetime(2026, 7, 14, 1, 2, 3, tzinfo=UTC),
        updated_at=datetime(2026, 7, 14, 4, 5, 6, tzinfo=UTC),
    )


def test_example_serializer_exposes_only_declared_attributes(example: Example) -> None:
    resource = ExampleSerializer.serialize(example)

    assert resource.attributes == {
        "title": "Example item",
        "description": None,
        "status": "active",
        "score": 80,
        "createdAt": "2026-07-14T01:02:03+00:00",
        "updatedAt": "2026-07-14T04:05:06+00:00",
    }
    assert "categoryId" not in resource.attributes
    assert resource.links == {"self": f"/api/v1/examples/{UUID(int=1)}"}


def test_default_resource_location_preserves_example_link_contract(example: Example) -> None:
    assert ExampleSerializer.resource_location(example) == f"/api/v1/examples/{UUID(int=1)}"


def test_overridden_resource_location_drives_resource_and_relationship_links() -> None:
    class _CurrentParentSerializer(_OneParentSerializer):
        @classmethod
        def resource_location(cls, model: _Parent) -> str:
            return "/parents/current"

    parent = _Parent(id=UUID(int=1), child=_Child(id=UUID(int=2)))

    resource = _CurrentParentSerializer.serialize(parent)

    assert resource.links == {"self": "/parents/current"}
    assert resource.relationships["child"].links == {
        "self": "/parents/current/relationships/child",
        "related": "/parents/current/child",
    }


def test_example_serializer_emits_linkage_and_relationship_links(example: Example) -> None:
    resource = ExampleSerializer.serialize(example)
    category = resource.relationships["category"]
    tags = resource.relationships["tags"]

    assert category.data is not None and not isinstance(category.data, list)
    assert (category.data.type, category.data.id) == ("exampleCategories", str(UUID(int=10)))
    assert tags.data is not None and isinstance(tags.data, list)
    assert [(item.type, item.id) for item in tags.data] == [("exampleTags", str(UUID(int=20)))]
    assert category.links == {
        "self": f"/api/v1/examples/{UUID(int=1)}/relationships/category",
        "related": f"/api/v1/examples/{UUID(int=1)}/category",
    }
    assert tags.links == {
        "self": f"/api/v1/examples/{UUID(int=1)}/relationships/tags",
        "related": f"/api/v1/examples/{UUID(int=1)}/tags",
    }


def test_to_one_null_is_explicit_and_requested_empty_include_is_returned() -> None:
    model = Example(
        id=UUID(int=2),
        title="Uncategorized",
        description=None,
        status=ExampleStatus.DRAFT,
        score=1,
        category=None,
        tags=[],
        created_at=datetime(2026, 7, 14, tzinfo=UTC),
        updated_at=datetime(2026, 7, 14, tzinfo=UTC),
    )

    document = ExampleSerializer.document(model, include=("category", "tags"))
    dumped = document.model_dump(mode="json", exclude_none=True)

    assert dumped["data"]["relationships"]["category"]["data"] is None
    assert dumped["data"]["relationships"]["tags"]["data"] == []
    assert dumped["included"] == []


def test_document_without_include_omits_included_member(example: Example) -> None:
    document = ExampleSerializer.document(example)

    assert "included" not in document.model_fields_set
    assert "included" not in document.model_dump(mode="json", exclude_none=True)


def test_include_deduplicates_resources_and_preserves_first_seen_order(example: Example) -> None:
    document = ExampleSerializer.document([example, example], include=("category", "tags"))

    assert "included" in document.model_fields_set
    keys = [(item.type, item.id) for item in document.included]
    assert keys == [
        ("exampleCategories", str(UUID(int=10))),
        ("exampleTags", str(UUID(int=20))),
    ]
    assert len(keys) == len(set(keys))


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


def test_nested_include_terminates_cycles_without_losing_other_branches() -> None:
    root = _GraphNode(id=UUID(int=1), name="root")
    shared = _GraphNode(id=UUID(int=2), name="shared")
    left_leaf = _GraphNode(id=UUID(int=3), name="left leaf")
    right_leaf = _GraphNode(id=UUID(int=4), name="right leaf")
    root.left = shared
    root.right = shared
    shared.left = left_leaf
    shared.right = right_leaf
    left_leaf.left = root

    document = _GraphSerializer.document(root, include=("left.left.left", "right.right"))
    keys = [(item.type, item.id) for item in document.included]

    assert keys == [
        ("graphNodes", str(shared.id)),
        ("graphNodes", str(left_leaf.id)),
        ("graphNodes", str(right_leaf.id)),
    ]
    assert ("graphNodes", str(root.id)) not in keys


def test_primary_resources_are_never_repeated_in_included() -> None:
    first = _GraphNode(id=UUID(int=1), name="first")
    second = _GraphNode(id=UUID(int=2), name="second")
    shared = _GraphNode(id=UUID(int=3), name="shared", left=first)
    first.left = shared
    second.left = shared

    document = _GraphSerializer.document([first, second], include=("left.left",))
    keys = {(item.type, item.id) for item in document.included}

    assert keys == {("graphNodes", str(shared.id))}


@pytest.mark.parametrize("path", ["", ".category", "category.", "category..examples"])
def test_include_tree_rejects_empty_segments(path: str) -> None:
    with pytest.raises(ValueError, match="empty segment"):
        build_include_tree((path,))


@pytest.mark.parametrize("path", ["unknown", "category.unknown", "tags.unknown"])
def test_serializer_rejects_undeclared_include_paths(example: Example, path: str) -> None:
    with pytest.raises(ValueError, match="undeclared relationship"):
        ExampleSerializer.document(example, include=(path,))


def test_direct_serializer_call_rejects_undeclared_include_path(example: Example) -> None:
    with pytest.raises(ValueError, match="undeclared relationship"):
        ExampleSerializer.serialize(example, include=("category.unknown",))


def test_include_tree_has_no_artificial_numeric_depth_cap() -> None:
    path = ".".join(f"relationship_{index}" for index in range(100))

    tree = build_include_tree((path, path))

    cursor = tree
    for segment in path.split("."):
        assert tuple(cursor) == (segment,)
        cursor = cursor[segment]


def test_loader_paths_cover_direct_linkage_and_requested_nested_includes() -> None:
    assert ExampleSerializer.required_loader_paths() == ("category", "tags")
    assert ExampleSerializer.required_loader_paths(include=("category",)) == ("category", "tags")
    assert _ManyParentSerializer.required_loader_paths() == ("children",)


def test_serializer_builds_sqlalchemy_loaders_from_declared_relationships() -> None:
    loaders = ExampleSerializer.loader_options(Example)

    statement = select(Example).options(*loaders)
    assert len(statement._with_options) == 2


def test_relationship_definition_is_immutable() -> None:
    definition = RelationshipDefinition(attribute="child", serializer=_ChildSerializer, many=False)

    with pytest.raises(FrozenInstanceError):
        definition.many = True  # type: ignore[misc]


@pytest.mark.parametrize(
    ("serializer", "child", "message"),
    [
        (_ManyParentSerializer, None, "expects a sequence"),
        (_ManyParentSerializer, _Child(UUID(int=2)), "expects a sequence"),
        (_OneParentSerializer, [_Child(UUID(int=2))], "expects one resource or None"),
    ],
)
def test_declared_relationship_cardinality_must_match_runtime_shape(
    serializer: type[JsonApiSerializer[Any]],
    child: object,
    message: str,
) -> None:
    model = _Parent(id=UUID(int=1), child=child)

    with pytest.raises(RuntimeError, match=message):
        serializer.serialize(model)


def test_json_encoding_supports_only_type_safe_json_values() -> None:
    model = _Encoded(
        id=UUID(int=1),
        api_url="https://example.test",
        payload={
            "uuid": UUID(int=2),
            "status": _WireStatus.READY,
            "at": datetime(2026, 7, 14, tzinfo=UTC),
            "optional": None,
            "items": [1, True, {"nested": "value"}],
        },
    )

    resource = _EncodedSerializer.serialize(model)

    assert resource.attributes == {
        "apiUrl": "https://example.test",
        "payload": {
            "uuid": str(UUID(int=2)),
            "status": "ready",
            "at": "2026-07-14T00:00:00+00:00",
            "optional": None,
            "items": [1, True, {"nested": "value"}],
        },
    }


@pytest.mark.parametrize("value", [nan, inf, -inf, object(), {1: "not-a-string-key"}, (1, 2)])
def test_json_encoding_rejects_non_json_or_non_finite_values(value: object) -> None:
    model = _Encoded(id=UUID(int=1), api_url="https://example.test", payload=value)

    with pytest.raises(RuntimeError, match="payload"):
        _EncodedSerializer.serialize(model)


def test_transient_unset_relationships_use_safe_serialization_defaults() -> None:
    model = _example_without_relationship_values(UUID(int=30))

    resource = ExampleSerializer.serialize(model)

    assert resource.relationships["category"].data is None
    assert resource.relationships["tags"].data == []


def test_pending_unset_relationships_use_safe_serialization_defaults(db_session: Session) -> None:
    model = _example_without_relationship_values(UUID(int=31))
    db_session.add(model)

    resource = ExampleSerializer.serialize(model)

    assert resource.relationships["category"].data is None
    assert resource.relationships["tags"].data == []


def test_initialize_relationship_defaults_survives_add_and_flush(db_session: Session) -> None:
    model = _example_without_relationship_values(UUID(int=32))

    ExampleSerializer.initialize_relationship_defaults(model)
    assert model.category is None
    assert model.tags == []
    db_session.add(model)
    db_session.flush()

    resource = ExampleSerializer.serialize(model)

    assert resource.relationships["category"].data is None
    assert resource.relationships["tags"].data == []


@pytest.mark.parametrize(
    ("operation", "unloaded_relationship"),
    [
        # ``serialize`` rebuilds the ``category`` linkage from the still-loaded
        # ``category_id`` column, so ``tags`` is the relationship that must fail loudly.
        ("serialize", "tags"),
        ("initialize", "category"),
    ],
)
@pytest.mark.parametrize("detach", [False, True], ids=["persistent", "detached"])
def test_unloaded_relationship_fails_without_running_a_lazy_query(
    db_session: Session,
    db_engine: Engine,
    detach: bool,
    operation: str,
    unloaded_relationship: str,
) -> None:
    category = ExampleCategory(name="database")
    tag = ExampleTag(name="guard")
    model = Example(
        title="No lazy query",
        description=None,
        status=ExampleStatus.ACTIVE,
        score=100,
        category=category,
        tags=[tag],
    )
    db_session.add(model)
    db_session.flush()
    model_id = model.id
    db_session.expunge_all()
    loaded = db_session.scalars(select(Example).where(Example.id == model_id)).one()
    if detach:
        db_session.expunge(loaded)
    query_count = 0

    def count_queries(*_: object) -> None:
        nonlocal query_count
        query_count += 1

    event.listen(db_engine, "before_cursor_execute", count_queries)
    try:
        with pytest.raises(RuntimeError, match=f"unloaded relationship '{unloaded_relationship}'"):
            if operation == "serialize":
                ExampleSerializer.serialize(loaded)
            else:
                ExampleSerializer.initialize_relationship_defaults(loaded)
    finally:
        event.remove(db_engine, "before_cursor_execute", count_queries)

    assert query_count == 0


def test_eagerly_loaded_relationships_serialize(db_session: Session) -> None:
    category = ExampleCategory(name="loaded")
    tag = ExampleTag(name="assigned")
    model = Example(
        title="Loaded",
        description=None,
        status=ExampleStatus.ACTIVE,
        score=100,
        category=category,
        tags=[tag],
    )
    db_session.add(model)
    db_session.flush()
    model_id = model.id
    db_session.expunge_all()
    loaded = db_session.scalars(
        select(Example)
        .where(Example.id == model_id)
        .options(selectinload(Example.category), selectinload(Example.tags))
    ).one()

    resource = ExampleSerializer.serialize(loaded)

    assert resource.id == str(model_id)


def test_linkage_only_loader_options_skip_the_to_one_query_and_narrow_the_to_many(
    db_session: Session,
    db_engine: Engine,
) -> None:
    model_id = _persist_related_example(db_session, category_name="linkage", tag_name="linkage")
    db_session.expunge_all()
    statements: list[str] = []

    def record(_connection: object, _cursor: object, statement: str, *_: object) -> None:
        statements.append(statement)

    event.listen(db_engine, "before_cursor_execute", record)
    try:
        loaded = db_session.scalars(
            select(Example)
            .where(Example.id == model_id)
            .options(*ExampleSerializer.loader_options(Example, linkage_only=True))
        ).one()
        load_statements = list(statements)
        resource = ExampleSerializer.serialize(loaded)
    finally:
        event.remove(db_engine, "before_cursor_execute", record)

    assert len(load_statements) == 2
    assert len(statements) == 2
    assert all("categories" not in statement for statement in load_statements)
    tag_statement = load_statements[1]
    assert "tags.id" in tag_statement
    assert "tags.name" not in tag_statement

    db_session.expunge_all()
    fully_loaded = db_session.scalars(
        select(Example).where(Example.id == model_id).options(*ExampleSerializer.loader_options(Example))
    ).one()
    assert resource == ExampleSerializer.serialize(fully_loaded)


def test_default_loader_options_still_load_full_related_objects(
    db_session: Session,
    db_engine: Engine,
) -> None:
    model_id = _persist_related_example(db_session, category_name="full", tag_name="full")
    db_session.expunge_all()
    loaded = db_session.scalars(
        select(Example).where(Example.id == model_id).options(*ExampleSerializer.loader_options(Example))
    ).one()
    query_count = 0

    def count_queries(*_: object) -> None:
        nonlocal query_count
        query_count += 1

    event.listen(db_engine, "before_cursor_execute", count_queries)
    try:
        assert loaded.category is not None
        assert loaded.category.name == "full"
        assert [tag.name for tag in loaded.tags] == ["full"]
    finally:
        event.remove(db_engine, "before_cursor_execute", count_queries)

    assert query_count == 0


def test_unloaded_relationship_without_a_loaded_foreign_key_still_fails(
    db_session: Session,
    db_engine: Engine,
) -> None:
    model_id = _persist_related_example(db_session, category_name="deferred", tag_name="deferred")
    db_session.expunge_all()
    loaded = db_session.scalars(select(Example).where(Example.id == model_id).options(defer(Example.category_id))).one()
    query_count = 0

    def count_queries(*_: object) -> None:
        nonlocal query_count
        query_count += 1

    event.listen(db_engine, "before_cursor_execute", count_queries)
    try:
        with pytest.raises(RuntimeError, match="unloaded relationship 'category'"):
            ExampleSerializer.serialize(loaded)
    finally:
        event.remove(db_engine, "before_cursor_execute", count_queries)

    assert query_count == 0


class _NonOrmLinkageParentSerializer(JsonApiSerializer[_Parent]):
    type_name = "parents"
    resource_path = None
    attributes = ()
    relationships: ClassVar[dict[str, RelationshipDefinition]] = {
        "child": RelationshipDefinition(
            attribute="child",
            serializer=_ChildSerializer,
            many=False,
            linkage_attribute="child_id",
        )
    }


class _UnmappedStateLinkageSerializer(JsonApiSerializer[Example]):
    type_name = "examples"
    resource_path = None
    attributes = ()
    relationships: ClassVar[dict[str, RelationshipDefinition]] = {
        "category": RelationshipDefinition(
            attribute="category",
            serializer=_ChildSerializer,
            many=False,
            linkage_attribute="missing_column",
        )
    }


def test_linkage_hint_is_ignored_for_a_model_without_orm_state() -> None:
    parent = _Parent(id=UUID(int=1), child=_Child(id=UUID(int=2)))

    resource = _NonOrmLinkageParentSerializer.serialize(parent)

    linkage = resource.relationships["child"].data
    assert linkage is not None and not isinstance(linkage, list)
    assert (linkage.type, linkage.id) == ("children", str(UUID(int=2)))


def test_linkage_hint_naming_an_unmapped_column_falls_back_to_the_unloaded_guard(
    db_session: Session,
) -> None:
    model_id = _persist_related_example(db_session, category_name="unmapped", tag_name="unmapped")
    db_session.expunge_all()
    loaded = db_session.scalars(select(Example).where(Example.id == model_id)).one()

    with pytest.raises(JsonApiSerializationError, match="unloaded relationship 'category'"):
        _UnmappedStateLinkageSerializer.serialize(loaded)


class _RelationshipLinkageSerializer(JsonApiSerializer[Example]):
    type_name = "examples"
    resource_path = None
    attributes = ()
    relationships: ClassVar[dict[str, RelationshipDefinition]] = {
        "category": RelationshipDefinition(
            attribute="category",
            serializer=_ChildSerializer,
            many=False,
            linkage_attribute="tags",
        )
    }


class _BadCardinalityLinkageSerializer(JsonApiSerializer[Example]):
    type_name = "examples"
    resource_path = None
    attributes = ()
    relationships: ClassVar[dict[str, RelationshipDefinition]] = {
        "tags": RelationshipDefinition(
            attribute="tags",
            serializer=_ChildSerializer,
            many=True,
            linkage_attribute="category_id",
        )
    }


class _UnmappedLinkageSerializer(JsonApiSerializer[Example]):
    type_name = "examples"
    resource_path = None
    attributes = ()
    relationships: ClassVar[dict[str, RelationshipDefinition]] = {
        "category": RelationshipDefinition(
            attribute="category",
            serializer=_ChildSerializer,
            many=False,
            linkage_attribute="not_a_column",
        )
    }


class _WrongColumnLinkageSerializer(JsonApiSerializer[Example]):
    type_name = "examples"
    resource_path = None
    attributes = ()
    relationships: ClassVar[dict[str, RelationshipDefinition]] = {
        "category": RelationshipDefinition(
            attribute="category",
            serializer=_ChildSerializer,
            many=False,
            linkage_attribute="score",
        )
    }


@pytest.mark.parametrize(
    ("serializer", "message"),
    [
        (_BadCardinalityLinkageSerializer, "only valid on a to-one relationship"),
        (_UnmappedLinkageSerializer, "is not mapped on Example"),
        (_WrongColumnLinkageSerializer, "is not the foreign key of 'category'"),
        (_RelationshipLinkageSerializer, "is not a column on Example"),
    ],
)
@pytest.mark.parametrize("linkage_only", [False, True], ids=["full", "linkage-only"])
def test_linkage_attribute_must_be_the_relationship_foreign_key(
    serializer: type[JsonApiSerializer[Example]],
    message: str,
    linkage_only: bool,
) -> None:
    with pytest.raises(JsonApiSerializationError, match=message):
        serializer.loader_options(Example, linkage_only=linkage_only)


class _JoinBase(DeclarativeBase):
    """Declarative base for join shapes the application models do not contain."""


class _JoinTarget(_JoinBase):
    __tablename__ = "join_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(20), nullable=False)


class _JoinOwner(_JoinBase):
    __tablename__ = "join_owners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int | None] = mapped_column(ForeignKey("join_targets.id"), nullable=True)

    target: Mapped[_JoinTarget | None] = relationship(_JoinTarget, foreign_keys=[target_id])
    kept_target: Mapped[_JoinTarget | None] = relationship(
        _JoinTarget,
        primaryjoin=lambda: and_(_JoinOwner.target_id == _JoinTarget.id, _JoinTarget.name == "keep"),
        viewonly=True,
        uselist=False,
    )


class _JoinTargetSerializer(JsonApiSerializer[_JoinTarget]):
    type_name = "joinTargets"
    resource_path = None
    attributes = ()


class _PlainJoinOwnerSerializer(JsonApiSerializer[_JoinOwner]):
    type_name = "joinOwners"
    resource_path = None
    attributes = ()
    relationships: ClassVar[dict[str, RelationshipDefinition]] = {
        "target": RelationshipDefinition(
            attribute="target",
            serializer=_JoinTargetSerializer,
            many=False,
            linkage_attribute="target_id",
        )
    }


class _FilteredJoinOwnerSerializer(JsonApiSerializer[_JoinOwner]):
    type_name = "joinOwners"
    resource_path = None
    attributes = ()
    relationships: ClassVar[dict[str, RelationshipDefinition]] = {
        "keptTarget": RelationshipDefinition(
            attribute="kept_target",
            serializer=_JoinTargetSerializer,
            many=False,
            linkage_attribute="target_id",
        )
    }


@pytest.mark.parametrize("linkage_only", [False, True], ids=["full", "linkage-only"])
def test_linkage_attribute_is_refused_when_the_join_carries_extra_criteria(linkage_only: bool) -> None:
    """The foreign key only determines linkage when it is the whole join condition.

    A ``primaryjoin`` with an extra predicate leaves one local/remote pair, so the pair
    and target-primary-key checks both pass. The shortcut would then advertise a target
    the relationship itself excludes, making the same resource contradict itself between
    a plain read and an ``include`` of the very same relationship.
    """

    with pytest.raises(JsonApiSerializationError, match="carries criteria beyond the foreign key"):
        _FilteredJoinOwnerSerializer.loader_options(_JoinOwner, linkage_only=linkage_only)


@pytest.mark.parametrize("linkage_only", [False, True], ids=["full", "linkage-only"])
def test_linkage_attribute_is_accepted_for_a_plain_foreign_key_join(linkage_only: bool) -> None:
    """The new check must not reject the ordinary shape it is meant to protect."""

    assert _PlainJoinOwnerSerializer.loader_options(_JoinOwner, linkage_only=linkage_only) is not None
    assert ExampleSerializer.loader_options(Example, linkage_only=linkage_only) is not None


def _persist_related_example(session: Session, *, category_name: str, tag_name: str) -> UUID:
    model = Example(
        title="Related",
        description=None,
        status=ExampleStatus.ACTIVE,
        score=100,
        category=ExampleCategory(name=category_name),
        tags=[ExampleTag(name=tag_name)],
    )
    session.add(model)
    session.flush()
    return model.id


def _example_without_relationship_values(identifier: UUID) -> Example:
    return Example(
        id=identifier,
        title=f"Example {identifier.int}",
        description=None,
        status=ExampleStatus.ACTIVE,
        score=50,
        created_at=datetime(2026, 7, 14, tzinfo=UTC),
        updated_at=datetime(2026, 7, 14, tzinfo=UTC),
    )


def test_included_reference_resources_carry_self_links(example: Example) -> None:
    document = ExampleSerializer.document(example, include=("category", "tags"))

    links = {item.type: item.links["self"] for item in document.included}

    assert links == {
        "exampleCategories": f"/api/v1/categories/{UUID(int=10)}",
        "exampleTags": f"/api/v1/tags/{UUID(int=20)}",
    }

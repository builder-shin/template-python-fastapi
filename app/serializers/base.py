"""Declarative, type-safe JSON:API resource serialization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from functools import lru_cache
from math import isfinite
from types import MappingProxyType
from typing import Any, ClassVar
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from pydantic import ConfigDict, TypeAdapter, ValidationError
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.orm import InstrumentedAttribute, selectinload
from sqlalchemy.orm.base import NO_VALUE
from sqlalchemy.orm.interfaces import LoaderOption
from sqlalchemy.orm.properties import ColumnProperty, RelationshipProperty

from app.jsonapi.documents import (
    JsonValue,
    Links,
    RelationshipObject,
    ResourceIdentifier,
    ResourceObject,
    SuccessDocument,
)
from app.jsonapi.naming import snake_to_camel

type IncludeTree = dict[str, IncludeTree]
type ResourceKey = tuple[str, str]
type VisitKey = tuple[str, str, tuple[str, ...]]
type LoaderPath = tuple[str, bool]

_LOADER_CACHE_SIZE = 512

_JSON_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(
    JsonValue,
    config=ConfigDict(allow_inf_nan=False),
)


class JsonApiSerializationError(RuntimeError):
    """Raised when declared serializer data cannot be emitted safely."""


@dataclass(frozen=True, slots=True)
class RelationshipDefinition:
    """Map a public relationship name to one internal model attribute.

    ``linkage_attribute`` is optional and only legal on a to-one relationship. It names
    the local foreign key column attribute backing that exact relationship, which lets a
    linkage-only read derive ``{"type", "id"}`` without loading the related row at all.
    """

    attribute: str
    serializer: type[JsonApiSerializer[Any]]
    many: bool
    linkage_attribute: str | None = None


@dataclass(slots=True)
class SerializationContext:
    """State shared while building one JSON:API compound document."""

    include_paths: IncludeTree
    included: dict[ResourceKey, ResourceObject] = field(default_factory=dict)
    primary_keys: set[ResourceKey] = field(default_factory=set)
    visited: set[VisitKey] = field(default_factory=set)


def build_include_tree(include: Sequence[str]) -> IncludeTree:
    """Parse full include paths without imposing an arbitrary depth limit."""

    tree: IncludeTree = {}
    for path in include:
        segments = path.split(".")
        if any(not segment.strip() for segment in segments):
            raise ValueError(f"include path {path!r} contains an empty segment")

        cursor = tree
        for segment in segments:
            cursor = cursor.setdefault(segment, {})
    return tree


class JsonApiSerializer[ModelT]:
    """Serialize only explicitly declared attributes and relationships."""

    type_name: ClassVar[str]
    resource_path: ClassVar[str | None] = None
    attributes: ClassVar[Sequence[str]] = ()
    relationships: ClassVar[Mapping[str, RelationshipDefinition]] = MappingProxyType({})

    @classmethod
    def resource_location(cls, model: ModelT) -> str | None:
        """Return the canonical self location for one resource, when available."""

        if cls.resource_path is None:
            return None
        return f"{cls.resource_path}/{cls._resource_key(model)[1]}"

    @classmethod
    def serialize(cls, model: ModelT, include: Sequence[str] = ()) -> ResourceObject:
        """Serialize one primary resource and validate any requested include path."""

        include_tree = build_include_tree(include)
        cls._validate_include_tree(include_tree)
        context = SerializationContext(include_paths=include_tree)
        context.primary_keys.add(cls._resource_key(model))
        return cls._serialize_resource(
            model,
            context=context,
            include_tree=include_tree,
            branch=(),
            register_included=False,
        )

    @classmethod
    def document(
        cls,
        models: ModelT | Sequence[ModelT] | None,
        include: Sequence[str] = (),
    ) -> SuccessDocument:
        """Build a JSON:API success document with deterministic compound resources."""

        include_tree = build_include_tree(include)
        cls._validate_include_tree(include_tree)
        context = SerializationContext(include_paths=include_tree)

        if models is None:
            primary_models: list[ModelT] = []
            is_collection = False
        elif isinstance(models, Sequence) and not isinstance(models, (str, bytes, bytearray)):
            primary_models = list(models)
            is_collection = True
        else:
            primary_models = [models]
            is_collection = False

        context.primary_keys.update(cls._resource_key(model) for model in primary_models)
        serialized = [
            cls._serialize_resource(
                model,
                context=context,
                include_tree=include_tree,
                branch=(),
                register_included=False,
            )
            for model in primary_models
        ]
        if models is None:
            data: ResourceObject | list[ResourceObject] | None = None
        elif is_collection:
            data = serialized
        else:
            data = serialized[0]

        if include:
            return SuccessDocument(data=data, included=list(context.included.values()))
        return SuccessDocument(data=data)

    @classmethod
    def required_loader_paths(cls, include: Sequence[str] = ()) -> tuple[str, ...]:
        """Return public relationship paths needed for linkage and requested includes."""

        return tuple(path for path, _ in cls.loader_paths(include))

    @classmethod
    def loader_paths(cls, include: Sequence[str] = ()) -> tuple[LoaderPath, ...]:
        """Return every loader path with a flag telling whether include requested it."""

        # A generic class object does not satisfy mypy's Hashable protocol check on an
        # lru_cache call site, so the hashable key is passed through Any deliberately.
        serializer: Any = cls
        return _cached_loader_paths(serializer, tuple(include))

    @classmethod
    def loader_options(
        cls,
        model_class: type[ModelT],
        include: Sequence[str] = (),
        *,
        linkage_only: bool = False,
    ) -> tuple[LoaderOption, ...]:
        """Derive eager loaders from serializer relationship declarations.

        With ``linkage_only`` the paths that include did not request are reduced to what
        relationship linkage actually needs: a to-one that declares ``linkage_attribute``
        is not loaded at all, and a to-many reads only the target primary key.
        """

        serializer: Any = cls
        target: Any = model_class
        return _cached_loader_options(serializer, target, tuple(include), linkage_only)

    @classmethod
    def _build_loader_options(
        cls,
        model_class: type[Any],
        include: tuple[str, ...],
        linkage_only: bool,
    ) -> tuple[LoaderOption, ...]:
        options: list[LoaderOption] = []
        for path, requested in cls.loader_paths(include):
            serializer: type[JsonApiSerializer[Any]] = cls
            current_model: type[Any] = model_class
            option: Any = None
            segments = path.split(".")
            for index, public_name in enumerate(segments):
                definition = serializer.relationships[public_name]
                attribute = getattr(current_model, definition.attribute, None)
                if not isinstance(attribute, InstrumentedAttribute):
                    raise JsonApiSerializationError(
                        f"declared relationship {definition.attribute!r} is not mapped on {current_model.__name__}"
                    )
                relationship = attribute.property
                if not isinstance(relationship, RelationshipProperty):
                    raise JsonApiSerializationError(
                        f"declared relationship {definition.attribute!r} is not a relationship on {current_model.__name__}"
                    )
                if bool(relationship.uselist) != definition.many:
                    raise JsonApiSerializationError(
                        f"declared relationship {definition.attribute!r} cardinality does not match {current_model.__name__}"
                    )
                if definition.linkage_attribute is not None:
                    _validate_linkage_attribute(current_model, definition, relationship)

                linkage_segment = linkage_only and not requested and index == len(segments) - 1
                if linkage_segment and definition.linkage_attribute is not None:
                    option = None
                    break
                option = selectinload(attribute) if option is None else option.selectinload(attribute)
                if linkage_segment:
                    option = option.load_only(_linkage_primary_key(relationship), raiseload=True)
                current_model = relationship.mapper.class_
                serializer = definition.serializer
            if option is not None:
                options.append(option)
        return tuple(options)

    @classmethod
    def _compute_loader_paths(cls, include: tuple[str, ...]) -> tuple[LoaderPath, ...]:
        include_tree = build_include_tree(include)
        cls._validate_include_tree(include_tree)
        paths: list[LoaderPath] = []
        seen: set[str] = set()
        cls._collect_loader_paths(include_tree, prefix=(), paths=paths, seen=seen)
        return tuple(paths)

    @classmethod
    def _collect_loader_paths(
        cls,
        include_tree: IncludeTree,
        *,
        prefix: tuple[str, ...],
        paths: list[LoaderPath],
        seen: set[str],
    ) -> None:
        for public_name in cls.relationships:
            path = ".".join((*prefix, public_name))
            if path not in seen:
                seen.add(path)
                paths.append((path, public_name in include_tree))

        for public_name, subtree in include_tree.items():
            definition = cls.relationships[public_name]
            definition.serializer._collect_loader_paths(
                subtree,
                prefix=(*prefix, public_name),
                paths=paths,
                seen=seen,
            )

    @classmethod
    def initialize_relationship_defaults(cls, model: ModelT) -> None:
        """Materialize safe relationship defaults before a new ORM object is persisted."""

        state = sqlalchemy_inspect(model, raiseerr=False)
        if state is None or not hasattr(state, "attrs"):
            return

        for definition in cls.relationships.values():
            try:
                attribute_state = state.attrs[definition.attribute]
            except KeyError as error:
                raise JsonApiSerializationError(
                    f"declared relationship {definition.attribute!r} is not mapped on {type(model).__name__}"
                ) from error
            if attribute_state.loaded_value is not NO_VALUE:
                continue
            if not (state.transient or state.pending):
                raise cls._unloaded_relationship_error(model, definition)
            setattr(model, definition.attribute, [] if definition.many else None)

    @classmethod
    def _validate_include_tree(
        cls,
        include_tree: IncludeTree,
        *,
        prefix: tuple[str, ...] = (),
    ) -> None:
        for public_name, subtree in include_tree.items():
            path = ".".join((*prefix, public_name))
            definition = cls.relationships.get(public_name)
            if definition is None:
                raise ValueError(f"undeclared relationship in include path {path!r}")
            definition.serializer._validate_include_tree(subtree, prefix=(*prefix, public_name))

    @classmethod
    def _serialize_resource(
        cls,
        model: Any,
        *,
        context: SerializationContext,
        include_tree: IncludeTree,
        branch: tuple[str, ...],
        register_included: bool,
    ) -> ResourceObject:
        resource, relationship_models = cls._build_resource(model)
        key = (resource.type, resource.id)
        if register_included and key not in context.primary_keys:
            context.included.setdefault(key, resource)

        for public_name, subtree in include_tree.items():
            definition = cls.relationships[public_name]
            relationship_branch = (*branch, public_name)
            for related_model in relationship_models[public_name]:
                related_key = definition.serializer._resource_key(related_model)
                visit_key = (*related_key, relationship_branch)
                if visit_key in context.visited:
                    continue
                context.visited.add(visit_key)
                definition.serializer._serialize_resource(
                    related_model,
                    context=context,
                    include_tree=subtree,
                    branch=relationship_branch,
                    register_included=True,
                )
        return resource

    @classmethod
    def _build_resource(cls, model: Any) -> tuple[ResourceObject, dict[str, tuple[Any, ...]]]:
        identifier = cls._identifier(model)
        resource_location = cls.resource_location(model)
        attributes: dict[str, JsonValue] = {}
        for internal_name in cls.attributes:
            public_name = snake_to_camel(internal_name)
            if public_name in attributes:
                raise JsonApiSerializationError(f"duplicate public attribute name {public_name!r}")
            value = getattr(model, internal_name)
            attributes[public_name] = _encode_json_value(value, attribute=internal_name)

        relationships: dict[str, RelationshipObject] = {}
        relationship_models: dict[str, tuple[Any, ...]] = {}
        for public_name, definition in cls.relationships.items():
            linkage: ResourceIdentifier | list[ResourceIdentifier] | None
            foreign_key_linkage = cls._foreign_key_linkage(model, definition)
            if foreign_key_linkage is not None:
                relationship_models[public_name] = ()
                linkage = foreign_key_linkage[0]
            else:
                value = cls._read_relationship(model, definition)
                related_models = _normalize_relationship(
                    value,
                    many=definition.many,
                    public_name=public_name,
                )
                relationship_models[public_name] = related_models
                if definition.many:
                    linkage = [definition.serializer._identifier(related_model) for related_model in related_models]
                elif related_models:
                    linkage = definition.serializer._identifier(related_models[0])
                else:
                    linkage = None

            if resource_location is None:
                relationships[public_name] = RelationshipObject(data=linkage)
            else:
                relationships[public_name] = RelationshipObject(
                    data=linkage,
                    links={
                        "self": f"{resource_location}/relationships/{public_name}",
                        "related": f"{resource_location}/{public_name}",
                    },
                )

        if relationships and resource_location is not None:
            links: Links = {"self": resource_location}
            return (
                ResourceObject(
                    type=identifier.type,
                    id=identifier.id,
                    attributes=attributes,
                    relationships=relationships,
                    links=links,
                ),
                relationship_models,
            )
        if relationships:
            return (
                ResourceObject(
                    type=identifier.type,
                    id=identifier.id,
                    attributes=attributes,
                    relationships=relationships,
                ),
                relationship_models,
            )
        if resource_location is not None:
            links = {"self": resource_location}
            return (
                ResourceObject(
                    type=identifier.type,
                    id=identifier.id,
                    attributes=attributes,
                    links=links,
                ),
                relationship_models,
            )
        return (
            ResourceObject(
                type=identifier.type,
                id=identifier.id,
                attributes=attributes,
            ),
            relationship_models,
        )

    @classmethod
    def _foreign_key_linkage(
        cls,
        model: Any,
        definition: RelationshipDefinition,
    ) -> tuple[ResourceIdentifier | None] | None:
        """Derive to-one linkage from the local foreign key of an unloaded relationship.

        Returns ``None`` when the shortcut does not apply so the caller falls back to
        reading the relationship, which keeps the unloaded-relationship guard in place
        whenever the foreign key column itself is not loaded either.
        """

        linkage_attribute = definition.linkage_attribute
        if linkage_attribute is None or definition.many:
            return None
        state = sqlalchemy_inspect(model, raiseerr=False)
        if state is None or not hasattr(state, "attrs"):
            return None
        if not (state.persistent or state.detached):
            return None
        try:
            relationship_state = state.attrs[definition.attribute]
            linkage_state = state.attrs[linkage_attribute]
        except KeyError:
            return None
        if relationship_state.loaded_value is not NO_VALUE:
            return None
        foreign_key = linkage_state.loaded_value
        if foreign_key is NO_VALUE:
            return None
        if foreign_key is None:
            return (None,)
        return (ResourceIdentifier(type=definition.serializer.type_name, id=str(foreign_key)),)

    @classmethod
    def _read_relationship(cls, model: Any, definition: RelationshipDefinition) -> Any:
        state = sqlalchemy_inspect(model, raiseerr=False)
        if state is not None and hasattr(state, "attrs"):
            try:
                attribute_state = state.attrs[definition.attribute]
            except KeyError as error:
                raise JsonApiSerializationError(
                    f"declared relationship {definition.attribute!r} is not mapped on {type(model).__name__}"
                ) from error
            if attribute_state.loaded_value is NO_VALUE:
                if state.transient or state.pending:
                    return [] if definition.many else None
                raise cls._unloaded_relationship_error(model, definition)
        return getattr(model, definition.attribute)

    @classmethod
    def _unloaded_relationship_error(
        cls,
        model: Any,
        definition: RelationshipDefinition,
    ) -> JsonApiSerializationError:
        return JsonApiSerializationError(
            f"unloaded relationship {definition.attribute!r} on {type(model).__name__}; "
            "eager-load every public path returned by required_loader_paths()"
        )

    @classmethod
    def _identifier(cls, model: Any) -> ResourceIdentifier:
        resource_type, resource_id = cls._resource_key(model)
        return ResourceIdentifier(type=resource_type, id=resource_id)

    @classmethod
    def _resource_key(cls, model: Any) -> ResourceKey:
        identifier = getattr(model, "id", None)
        if identifier is None:
            raise JsonApiSerializationError(f"{cls.__name__} requires a non-null model id")
        return cls.type_name, str(identifier)


@lru_cache(maxsize=_LOADER_CACHE_SIZE)
def _cached_loader_paths(
    serializer: type[JsonApiSerializer[Any]],
    include: tuple[str, ...],
) -> tuple[LoaderPath, ...]:
    """Memoize the include parse and validation; failures are never memoized."""

    return serializer._compute_loader_paths(include)


@lru_cache(maxsize=_LOADER_CACHE_SIZE)
def _cached_loader_options(
    serializer: type[JsonApiSerializer[Any]],
    model_class: type[Any],
    include: tuple[str, ...],
    linkage_only: bool,
) -> tuple[LoaderOption, ...]:
    """Memoize loader chains; SQLAlchemy loader options are immutable and shareable."""

    return serializer._build_loader_options(model_class, include, linkage_only)


def _validate_linkage_attribute(
    model_class: type[Any],
    definition: RelationshipDefinition,
    relationship: RelationshipProperty[Any],
) -> None:
    linkage_attribute = definition.linkage_attribute
    if definition.many:
        raise JsonApiSerializationError(
            f"declared linkage attribute {linkage_attribute!r} is only valid on a to-one relationship "
            f"on {model_class.__name__}"
        )
    attribute = getattr(model_class, linkage_attribute, None) if linkage_attribute is not None else None
    if not isinstance(attribute, InstrumentedAttribute):
        raise JsonApiSerializationError(
            f"declared linkage attribute {linkage_attribute!r} is not mapped on {model_class.__name__}"
        )
    column_property = attribute.property
    if not isinstance(column_property, ColumnProperty):
        raise JsonApiSerializationError(
            f"declared linkage attribute {linkage_attribute!r} is not a column on {model_class.__name__}"
        )
    pairs = tuple(relationship.local_remote_pairs or ())
    columns = tuple(column_property.columns)
    if len(pairs) != 1 or len(columns) != 1 or columns[0] is not pairs[0][0]:
        raise JsonApiSerializationError(
            f"declared linkage attribute {linkage_attribute!r} is not the foreign key of "
            f"{definition.attribute!r} on {model_class.__name__}"
        )
    target_primary_key = relationship.mapper.primary_key
    if len(target_primary_key) != 1 or pairs[0][1] is not target_primary_key[0]:
        raise JsonApiSerializationError(
            f"declared linkage attribute {linkage_attribute!r} must reference the primary key of "
            f"{relationship.mapper.class_.__name__}"
        )
    local, remote = pairs[0]
    if relationship.secondary is not None or not relationship.primaryjoin.compare(local == remote):
        raise JsonApiSerializationError(
            f"declared linkage attribute {linkage_attribute!r} cannot be used for "
            f"{definition.attribute!r} on {model_class.__name__}: the relationship join "
            "carries criteria beyond the foreign key"
        )


def _linkage_primary_key(relationship: RelationshipProperty[Any]) -> InstrumentedAttribute[Any]:
    mapper = relationship.mapper
    primary_key = mapper.primary_key
    if len(primary_key) != 1:
        raise JsonApiSerializationError(
            f"linkage-only loading requires a single primary key column on {mapper.class_.__name__}"
        )
    attribute = getattr(mapper.class_, mapper.get_property_by_column(primary_key[0]).key, None)
    if not isinstance(attribute, InstrumentedAttribute):
        raise JsonApiSerializationError(f"primary key of {mapper.class_.__name__} is not mapped as an attribute")
    return attribute


def _normalize_relationship(value: Any, *, many: bool, public_name: str) -> tuple[Any, ...]:
    is_sequence = isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
    if many:
        if value is None or not is_sequence:
            raise JsonApiSerializationError(f"relationship {public_name!r} expects a sequence")
        return tuple(value)
    if is_sequence:
        raise JsonApiSerializationError(f"relationship {public_name!r} expects one resource or None")
    if value is None:
        return ()
    return (value,)


def _encode_json_value(value: object, *, attribute: str) -> JsonValue:
    try:
        _validate_json_input(value)
        encoded = jsonable_encoder(value)
        return _JSON_VALUE_ADAPTER.validate_python(encoded)
    except (TypeError, ValueError, ValidationError) as error:
        raise JsonApiSerializationError(
            f"attribute {attribute!r} cannot be encoded as a finite JSON value: {error}"
        ) from error


def _validate_json_input(value: object) -> None:
    if value is None or isinstance(value, (str, int, bool, UUID, StrEnum, datetime)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("NaN and Infinity are not valid JSON values")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_input(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            _validate_json_input(item)
        return
    raise TypeError(f"unsupported JSON value type: {type(value).__name__}")

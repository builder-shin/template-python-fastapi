"""Declarative, type-safe JSON:API resource serialization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
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
from sqlalchemy.orm.properties import RelationshipProperty

from app.jsonapi.documents import (
    JsonValue,
    Links,
    RelationshipObject,
    ResourceIdentifier,
    ResourceObject,
    SuccessDocument,
)

type IncludeTree = dict[str, IncludeTree]
type ResourceKey = tuple[str, str]
type VisitKey = tuple[str, str, tuple[str, ...]]

_JSON_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(
    JsonValue,
    config=ConfigDict(allow_inf_nan=False),
)


class JsonApiSerializationError(RuntimeError):
    """Raised when declared serializer data cannot be emitted safely."""


@dataclass(frozen=True, slots=True)
class RelationshipDefinition:
    """Map a public relationship name to one internal model attribute."""

    attribute: str
    serializer: type[JsonApiSerializer[Any]]
    many: bool


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

        include_tree = build_include_tree(include)
        cls._validate_include_tree(include_tree)
        paths: list[str] = []
        seen: set[str] = set()
        cls._collect_loader_paths(include_tree, prefix=(), paths=paths, seen=seen)
        return tuple(paths)

    @classmethod
    def loader_options(
        cls,
        model_class: type[ModelT],
        include: Sequence[str] = (),
    ) -> tuple[LoaderOption, ...]:
        """Derive eager loaders from serializer relationship declarations."""

        options: list[LoaderOption] = []
        for path in cls.required_loader_paths(include):
            serializer: type[JsonApiSerializer[Any]] = cls
            current_model: type[Any] = model_class
            option: Any = None
            for public_name in path.split("."):
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
                option = selectinload(attribute) if option is None else option.selectinload(attribute)
                current_model = relationship.mapper.class_
                serializer = definition.serializer
            options.append(option)
        return tuple(options)

    @classmethod
    def _collect_loader_paths(
        cls,
        include_tree: IncludeTree,
        *,
        prefix: tuple[str, ...],
        paths: list[str],
        seen: set[str],
    ) -> None:
        for public_name in cls.relationships:
            path = ".".join((*prefix, public_name))
            if path not in seen:
                seen.add(path)
                paths.append(path)

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
            public_name = _snake_to_camel(internal_name)
            if public_name in attributes:
                raise JsonApiSerializationError(f"duplicate public attribute name {public_name!r}")
            value = getattr(model, internal_name)
            attributes[public_name] = _encode_json_value(value, attribute=internal_name)

        relationships: dict[str, RelationshipObject] = {}
        relationship_models: dict[str, tuple[Any, ...]] = {}
        for public_name, definition in cls.relationships.items():
            value = cls._read_relationship(model, definition)
            related_models = _normalize_relationship(
                value,
                many=definition.many,
                public_name=public_name,
            )
            relationship_models[public_name] = related_models
            if definition.many:
                linkage: ResourceIdentifier | list[ResourceIdentifier] | None = [
                    definition.serializer._identifier(related_model) for related_model in related_models
                ]
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


def _snake_to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(segment[:1].upper() + segment[1:] for segment in tail)


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

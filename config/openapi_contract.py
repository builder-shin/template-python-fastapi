"""Enrich generated OpenAPI using the same ORM, serializer and query declarations."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import inspect

from app.controllers.concerns.crud_actions import CrudActions
from app.jsonapi.naming import snake_to_camel
from app.jsonapi.query import QueryPolicy
from app.models import User
from app.schemas.auth import CredentialsAttributes
from app.serializers import UserSerializer
from app.serializers.base import JsonApiSerializer
from config.routes import example_categories_controller, example_tags_controller, examples_controller

MEDIA_TYPE = "application/vnd.api+json"
STRING = {"type": "string"}
VERSION = {"type": "object", "required": ["version"], "properties": {"version": {"type": "string", "enum": ["1.1"]}}}
PAGINATION_LINKS = {
    "type": "object",
    "required": ["self", "first", "prev", "next", "last"],
    "properties": {name: {"type": ["string", "null"]} for name in ["self", "first", "prev", "next", "last"]},
}


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties) if required is None else required}


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def _column_schema(column: Any) -> dict[str, Any]:
    python_type = column.type.python_type
    if isinstance(python_type, type) and issubclass(python_type, Enum):
        return {"type": "string", "enum": [entry.value for entry in python_type]}
    schema: dict[str, Any] = {"type": {int: "integer", bool: "boolean", float: "number"}.get(python_type, "string")}
    if python_type is datetime:
        schema["format"] = "date-time"
    elif python_type is UUID:
        schema["format"] = "uuid"
    if getattr(column.type, "length", None):
        schema["maxLength"] = column.type.length
    if column.nullable:
        schema["type"] = [schema["type"], "null"]
    return schema


def _resource(
    model: Any, serializer: type[JsonApiSerializer[Any]], write_schema: type[BaseModel] | None = None
) -> dict[str, Any]:
    columns = inspect(model).columns
    validation = write_schema.model_json_schema(by_alias=True).get("properties", {}) if write_schema else {}
    attributes: dict[str, Any] = {}
    for attribute in serializer.attributes:
        public = snake_to_camel(attribute)
        schema = _column_schema(columns[attribute])
        constraints = validation.get(public, {})
        for name in ["minLength", "maxLength", "minimum", "maximum", "format"]:
            if name in constraints:
                schema[name] = constraints[name]
        attributes[public] = schema
    relationships = {
        name: _object(
            {
                "data": _linkage(definition.serializer.type_name, definition.many),
                "links": _object({"self": STRING, "related": STRING}),
            }
        )
        for name, definition in serializer.relationships.items()
    }
    properties = {
        "type": {"type": "string", "enum": [serializer.type_name]},
        "id": {"type": "string", "format": "uuid"},
        "attributes": _object(attributes),
        "links": _object({"self": STRING}),
    }
    if relationships:
        properties["relationships"] = _object(relationships)
    return _object(properties)


def _linkage(type_name: str, many: bool) -> dict[str, Any]:
    identifier = {
        **_object(
            {"type": {"type": "string", "enum": [type_name]}, "id": STRING, "meta": {"type": "object"}}, ["type", "id"]
        ),
        "additionalProperties": False,
    }
    return (
        {"type": "array", "items": identifier, "uniqueItems": True}
        if many
        else {"anyOf": [identifier, {"type": "null"}]}
    )


def _success(data: dict[str, Any], collection: bool = False, included: list[str] | None = None) -> dict[str, Any]:
    properties = {"jsonapi": VERSION, "data": data}
    required = ["jsonapi", "data"]
    if collection:
        properties.update(links=PAGINATION_LINKS, meta=_object({"totalCount": {"type": "integer", "minimum": 0}}))
        required.append("links")
    if included:
        properties["included"] = {"type": "array", "items": {"oneOf": [_ref(name + "Resource") for name in included]}}
    else:
        properties["included"] = {"type": "array", "items": {"type": "object"}}
    return _object(properties, required)


def _parameter(name: str, schema: dict[str, Any], description: str = "") -> dict[str, Any]:
    return {"name": name, "in": "query", "required": False, "schema": schema, "description": description}


def _pages(related: bool = False) -> list[dict[str, Any]]:
    values = [
        _parameter(
            "page[number]",
            {"type": "integer", "minimum": 1, "maximum": 2**53},
            "Offset mode only. (number - 1) * clamped size must not exceed 9007199254740991.",
        ),
        _parameter(
            "page[size]",
            {"type": "integer", "minimum": 1, "format": "int64", "default": 20},
            "Positive int64, clamped to 100 before computing the offset.",
        ),
    ]
    if not related:
        values.extend(
            [
                _parameter(
                    "page[totals]",
                    {"type": "boolean", "default": False},
                    "Include totalCount and the last offset page.",
                ),
                *[
                    _parameter(
                        f"page[{direction}]",
                        {"type": "string", "maxLength": 4096},
                        "Opaque cursor; empty starts at the boundary. Cannot combine with number or the other cursor direction.",
                    )
                    for direction in ["after", "before"]
                ],
            ]
        )
    return values


def _include(policy: QueryPolicy) -> dict[str, Any]:
    return _parameter(
        "include",
        STRING,
        f"Comma-separated relationships: {', '.join(sorted(policy.includes))}. Empty requests included: [].",
    )


def _queries(policy: QueryPolicy) -> list[dict[str, Any]]:
    result = [
        *_pages(),
        _parameter(
            "sort",
            STRING,
            f"Comma-separated fields; prefix - for descending: {', '.join(policy.sorts)}. Default: {','.join(('-' if term.descending else '') + term.name for term in policy.default_sort)}.",
        ),
        _include(policy),
    ]
    for name, field in policy.filters.items():
        schema = _column_schema(field.column)
        schema.pop("maxLength", None)
        if isinstance(schema.get("type"), list):
            schema["type"] = schema["type"][0]
        if schema["type"] == "integer":
            schema.update(minimum=-(2**31), maximum=2**31 - 1)
        for operator in sorted(field.operators):
            value_schema = {"type": "boolean"} if operator == "isNull" else STRING if operator == "in" else schema
            description = (
                "Case-sensitive literal substring."
                if operator == "contains"
                else "Comma-separated values."
                if operator == "in"
                else operator
            )
            result.append(_parameter(f"filter[{name}][{operator}]", value_schema, description))
            if operator == "exact":
                result.append(_parameter(f"filter[{name}]", value_schema, "Alias of exact."))
    return result


def _response(operation: dict[str, Any] | None, schema: dict[str, Any], statuses: tuple[int, ...] = (200,)) -> None:
    if operation is None:
        return
    responses = operation.setdefault("responses", {})
    for status in statuses:
        response = responses.setdefault(str(status), {"description": "JSON:API success"})
        if status != 204:
            response["content"] = {MEDIA_TYPE: {"schema": schema}}
    for status, response in responses.items():
        if status.isdigit() and int(status) >= 400:
            response["content"] = {MEDIA_TYPE: {"schema": _ref("ErrorDocument")}}


def enrich_openapi(document: dict[str, Any]) -> None:
    schemas = document["components"]["schemas"]
    controllers: tuple[CrudActions[Any, Any, Any, Any], ...] = (
        examples_controller,
        example_categories_controller,
        example_tags_controller,
    )
    schemas["ErrorDocument"] = _object(
        {
            "jsonapi": VERSION,
            "errors": {
                "type": "array",
                "minItems": 1,
                "items": _object(
                    {
                        "status": STRING,
                        "code": STRING,
                        "title": STRING,
                        "detail": STRING,
                        "source": _object({"pointer": STRING, "parameter": STRING, "header": STRING}, []),
                        "meta": {"type": "object"},
                    },
                    ["status", "code", "title", "detail"],
                ),
            },
        }
    )
    for controller in controllers:
        serializer = controller.serializer_class
        resource_name = serializer.type_name + "Resource"
        schemas[resource_name] = _resource(
            controller.model_class, serializer, controller.create_schema if controller.enable_writes else None
        )
        path = serializer.resource_path or controller.prefix
        collection = document["paths"][path]
        item = document["paths"][path + "/{resource_id}"]
        included = [definition.serializer.type_name for definition in serializer.relationships.values()]
        _response(collection.get("get"), _success({"type": "array", "items": _ref(resource_name)}, True, included))
        collection["get"]["parameters"] = _queries(controller.query_policy)
        _response(item.get("get"), _success(_ref(resource_name), included=included))
        item["get"].setdefault("parameters", []).append(_include(controller.query_policy))
        for method, operation in [*(collection.items()), *(item.items())]:
            if method in ["post", "patch", "put", "delete"]:
                statuses = (
                    (201,)
                    if method == "post"
                    else (200, 201)
                    if method == "put"
                    else (204,)
                    if method == "delete"
                    else (200,)
                )
                _response(operation, _success(_ref(resource_name), included=included), statuses)
        for name, definition in serializer.relationships.items():
            linkage = _linkage(definition.serializer.type_name, definition.many)
            relationship = document["paths"][path + "/{resource_id}/relationships/" + name]
            _response(
                relationship.get("get"),
                _object({"jsonapi": VERSION, "data": linkage, "links": _object({"self": STRING, "related": STRING})}),
            )
            for method in ["post", "patch", "delete"]:
                if method in relationship:
                    _response(relationship[method], {}, (204,))
                    relationship[method]["requestBody"]["content"][MEDIA_TYPE]["schema"] = {
                        **_object({"data": linkage}),
                        "additionalProperties": False,
                    }
            related = document["paths"][path + "/{resource_id}/" + name]["get"]
            target = _ref(definition.serializer.type_name + "Resource")
            _response(
                related,
                _success(
                    {"type": "array", "items": target} if definition.many else {"anyOf": [target, {"type": "null"}]},
                    definition.many,
                ),
            )
            if definition.many:
                related.setdefault("parameters", []).extend(_pages(True))
        if controller.enable_writes:
            schemas["ExampleRelationships"] = {
                **_object(
                    {
                        name: {
                            **_object({"data": _linkage(definition.serializer.type_name, definition.many)}),
                            "additionalProperties": False,
                        }
                        for name, definition in serializer.relationships.items()
                    },
                    [],
                ),
                "minProperties": 1,
                "additionalProperties": False,
            }
            for operation in [collection["post"], item["put"]]:
                operation["responses"]["201"].setdefault(
                    "headers", {"Location": {"schema": STRING, "description": "Canonical URL of the created example."}}
                )
    schemas["usersResource"] = _resource(User, UserSerializer, CredentialsAttributes)
    _response(document["paths"]["/api/v1/users/me"]["get"], _success(_ref("usersResource")))
    _response(document["paths"]["/api/v1/auth/register"]["post"], _success(_ref("usersResource")), (201,))
    tokens = _object(
        {
            "type": {"type": "string", "enum": ["authTokens"]},
            "id": {"type": "string", "format": "uuid"},
            "attributes": _object(
                {
                    "accessToken": STRING,
                    "refreshToken": STRING,
                    "tokenType": {"type": "string", "enum": ["Bearer"]},
                    "expiresIn": {"type": "integer"},
                    "refreshExpiresIn": {"type": "integer"},
                }
            ),
        }
    )
    for action in ["login", "refresh", "logout"]:
        _response(
            document["paths"][f"/api/v1/auth/{action}"]["post"],
            _success(tokens),
            (204,) if action == "logout" else (200,),
        )
    document["paths"]["/api/v1/auth/register"]["post"]["responses"]["201"]["headers"] = {
        "Location": {"schema": {"type": "string", "enum": ["/api/v1/users/me"]}}
    }
    for path in ["/health/live", "/health/ready"]:
        _response(
            document["paths"][path]["get"],
            _object(
                {
                    "jsonapi": VERSION,
                    "data": {"type": "null"},
                    "meta": _object({"status": {"type": "string", "enum": ["ok"]}}),
                }
            ),
        )
    for name, schema in schemas.items():
        if name.startswith("ExamplesController") and name.endswith("Resource"):
            schema["properties"]["type"]["enum"] = ["examples"]
            if "Update" in name:
                schema["anyOf"] = [{"required": ["attributes"]}, {"required": ["relationships"]}]
        if name == "ExampleRelationships":
            schema["minProperties"] = 1


def install_openapi(app: FastAPI) -> None:
    generated = app.openapi
    enriched: dict[str, Any] | None = None

    def openapi() -> dict[str, Any]:
        nonlocal enriched
        if enriched is None:
            enriched = generated()
            enrich_openapi(enriched)
        return enriched

    app.openapi = openapi  # type: ignore[method-assign]

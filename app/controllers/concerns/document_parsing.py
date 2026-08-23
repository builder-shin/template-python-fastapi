"""Stateless JSON:API request-document parsing and validation helpers.

Every function here reads only the already-validated request document (plus, where a
check needs one, the expected type or the addressed id), so none of them touches the
controller instance, the session, or the router. The ``status_code``/``code``/
``source_pointer`` triples are the public error contract and must not drift.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Request
from pydantic import BaseModel
from pydantic.experimental.missing_sentinel import MISSING
from sqlalchemy import inspect as sqlalchemy_inspect

from app.jsonapi import JsonApiException


def document_data(document: BaseModel) -> BaseModel:
    """Return the primary ``data`` member of a write document."""

    data = getattr(document, "data", None)
    if not isinstance(data, BaseModel):
        raise JsonApiException(status_code=400, code="INVALID_JSONAPI_DOCUMENT")
    return data


def document_attributes(data: BaseModel) -> BaseModel:
    """Return the ``attributes`` member that create and replace require."""

    attributes = getattr(data, "attributes", None)
    if not isinstance(attributes, BaseModel):
        raise JsonApiException(status_code=400, code="INVALID_JSONAPI_DOCUMENT")
    return attributes


def document_relationships(data: BaseModel) -> BaseModel | None:
    """Return the optional ``relationships`` member of a write document."""

    relationships = getattr(data, "relationships", None)
    if relationships is MISSING or relationships is None:
        return None
    if not isinstance(relationships, BaseModel):
        raise JsonApiException(
            status_code=400,
            code="INVALID_JSONAPI_DOCUMENT",
            source_pointer="/data/relationships",
        )
    return relationships


def linkage_data(document: BaseModel) -> object:
    """Return the linkage member of a relationship document."""

    linkage = getattr(document, "data", MISSING)
    if linkage is MISSING:
        raise JsonApiException(
            status_code=400,
            code="INVALID_JSONAPI_DOCUMENT",
            source_pointer="/data",
        )
    return linkage


def require_resource_type(data: BaseModel, *, expected_type: str) -> None:
    """Reject a document whose ``data.type`` is not the controller's resource type."""

    if getattr(data, "type", None) != expected_type:
        raise JsonApiException(
            status_code=409,
            code="TYPE_MISMATCH",
            source_pointer="/data/type",
        )


def require_document_id(data: BaseModel, resource_id: str) -> None:
    """Reject a document whose ``data.id`` does not address the request path."""

    if getattr(data, "id", None) != resource_id:
        raise JsonApiException(
            status_code=409,
            code="ID_MISMATCH",
            source_pointer="/data/id",
        )


def reject_client_generated_id(data: BaseModel) -> None:
    """Reject a create document that carries a client-generated ``id``."""

    if getattr(data, "id", MISSING) is not MISSING:
        raise JsonApiException(
            status_code=403,
            code="CLIENT_GENERATED_ID_UNSUPPORTED",
            source_pointer="/data/id",
        )


def reject_query_parameters(request: Request) -> None:
    """Reject every query parameter on an action that declares none."""

    first_item = next(iter(request.query_params.multi_items()), None)
    if first_item is not None:
        raise JsonApiException(
            status_code=400,
            code="INVALID_QUERY_PARAMETER",
            source_parameter=first_item[0],
        )


def coerce_model_id(
    model_class: type[Any],
    resource_id: str,
    *,
    relationship: bool = False,
    source_pointer: str | None = None,
) -> object:
    """Coerce a URL or linkage id into the model's primary-key Python type."""

    mapper = sqlalchemy_inspect(model_class)
    if mapper is None:
        raise RuntimeError("CRUD model class is not mapped")
    primary_key = mapper.primary_key[0]
    try:
        python_type = primary_key.type.python_type
        if python_type is UUID:
            return UUID(resource_id)
        return python_type(resource_id)
    except (AttributeError, TypeError, ValueError):
        if relationship:
            raise JsonApiException(
                status_code=404,
                code="RELATIONSHIP_RESOURCE_NOT_FOUND",
                source_pointer=source_pointer,
            ) from None
        raise JsonApiException(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            source_pointer=source_pointer,
        ) from None

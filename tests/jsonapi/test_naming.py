"""Single-source public naming rule and strict write-input configuration."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.controllers.concerns import jsonapi_routes
from app.jsonapi.naming import WRITE_MODEL_CONFIG, JsonApiWriteSchema, snake_to_camel
from app.schemas import auth as auth_schemas
from app.schemas import example as example_schemas
from app.serializers import base as serializer_base

APP_ROOT = Path(__file__).resolve().parents[2] / "app"


@pytest.mark.parametrize(
    ("internal_name", "public_name"),
    [
        ("created_at", "createdAt"),
        ("refresh_token", "refreshToken"),
        ("id", "id"),
        ("createdAt", "createdAt"),
        ("category_id_value", "categoryIdValue"),
        ("_leading", "Leading"),
    ],
)
def test_snake_to_camel_maps_internal_names_to_public_names(internal_name: str, public_name: str) -> None:
    assert snake_to_camel(internal_name) == public_name


def test_only_one_snake_to_camel_definition_exists_in_the_application() -> None:
    """Acceptance criterion 1: request and response naming share one implementation."""

    pattern = re.compile(r"^def _?snake_to_camel\(", re.MULTILINE)
    definitions = [
        path for path in APP_ROOT.rglob("*.py") if pattern.search(path.read_text(encoding="utf-8")) is not None
    ]

    assert definitions == [APP_ROOT / "jsonapi" / "naming.py"]


def test_request_and_response_sides_use_the_same_naming_function() -> None:
    # Both modules re-export the shared helper; strict mypy forbids implicit re-export reads.
    assert serializer_base.snake_to_camel is snake_to_camel  # type: ignore[attr-defined]
    assert WRITE_MODEL_CONFIG["alias_generator"] is snake_to_camel
    assert jsonapi_routes.WRITE_MODEL_CONFIG is WRITE_MODEL_CONFIG  # type: ignore[attr-defined]


def test_write_model_config_is_strict_camelcase_and_forbids_unknown_members() -> None:
    assert WRITE_MODEL_CONFIG["extra"] == "forbid"
    assert WRITE_MODEL_CONFIG["populate_by_name"] is True
    assert WRITE_MODEL_CONFIG["strict"] is True


def test_json_api_write_schema_carries_the_shared_write_configuration() -> None:
    for key, value in WRITE_MODEL_CONFIG.items():
        assert JsonApiWriteSchema.model_config[key] is value  # type: ignore[literal-required]


@pytest.mark.parametrize(
    "schema",
    [
        auth_schemas.CredentialsAttributes,
        example_schemas.ExampleCreate,
        example_schemas.ExampleUpdate,
        example_schemas.ExampleReplace,
        example_schemas.ExampleRelationships,
    ],
)
def test_every_write_schema_inherits_the_shared_strict_base(schema: type[JsonApiWriteSchema]) -> None:
    assert issubclass(schema, JsonApiWriteSchema)
    assert schema.model_config["strict"] is True
    assert schema.model_config["extra"] == "forbid"


def test_write_base_rejects_unknown_members_and_accepts_camel_case_aliases() -> None:
    class Sample(JsonApiWriteSchema):
        refresh_token: str

    assert Sample.model_validate({"refreshToken": "value"}).refresh_token == "value"
    assert Sample.model_validate({"refresh_token": "value"}).refresh_token == "value"
    with pytest.raises(ValidationError):
        Sample.model_validate({"refreshToken": "value", "extra": 1})
    with pytest.raises(ValidationError):
        Sample.model_validate({"refreshToken": 1})

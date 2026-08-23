"""Strict JSON:API input contracts for the example write schemas."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from pydantic.experimental.missing_sentinel import MISSING

from app.jsonapi.naming import snake_to_camel
from app.models import ExampleStatus
from app.schemas.example import (
    ExampleCreate,
    ExampleRelationships,
    ExampleReplace,
    ExampleUpdate,
)


def _attributes(**overrides: Any) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "title": "예시",
        "description": None,
        "status": "active",
        "score": 50,
    }
    attributes.update(overrides)
    return attributes


@pytest.mark.parametrize("schema", [ExampleCreate, ExampleReplace])
@pytest.mark.parametrize(
    "overrides",
    [
        {"score": "50"},
        {"score": 50.0},
        {"score": True},
        {"score": 101},
        {"score": -1},
        {"title": 123},
        {"title": ""},
        {"description": 123},
        {"status": 0},
        {"status": "bogus"},
        {"unknown": "value"},
    ],
    ids=[
        "numeric-string-score",
        "float-score",
        "bool-score",
        "score-above-range",
        "negative-score",
        "non-string-title",
        "empty-title",
        "non-string-description",
        "non-string-status",
        "unknown-status",
        "unknown-attribute",
    ],
)
def test_example_write_schemas_reject_loose_python_input(
    schema: type[ExampleCreate | ExampleReplace],
    overrides: dict[str, Any],
) -> None:
    """FastAPI validates bodies with ``validate_python``, so strictness must hold there."""

    with pytest.raises(ValidationError):
        schema.model_validate(_attributes(**overrides))


@pytest.mark.parametrize("schema", [ExampleCreate, ExampleReplace])
def test_example_write_schemas_accept_the_json_string_form_of_the_status_enum(
    schema: type[ExampleCreate | ExampleReplace],
) -> None:
    """``strict=True`` on the base would reject ``"active"`` in Python validation mode."""

    model = schema.model_validate(_attributes(status="active"))

    assert model.status is ExampleStatus.ACTIVE
    assert model.title == "예시"
    assert model.description is None
    assert model.score == 50


def test_example_create_accepts_a_camel_case_alias_and_a_string_description() -> None:
    # Every field of the example write schemas is a single word, so alias == field name
    # and no payload built from `_attributes()` can exercise the alias contract. Assert on
    # the generator itself instead: dropping it from the shared write config leaves every
    # payload-level assertion in this file green.
    assert ExampleCreate.model_config["alias_generator"] is snake_to_camel
    assert ExampleCreate.model_config["populate_by_name"] is True

    model = ExampleCreate.model_validate(_attributes(description="설명"))

    assert model.description == "설명"


@pytest.mark.parametrize("schema", [ExampleCreate, ExampleReplace, ExampleUpdate, ExampleRelationships])
def test_example_write_schemas_share_the_single_camel_case_name_rule(
    schema: type[ExampleCreate | ExampleReplace | ExampleUpdate | ExampleRelationships],
) -> None:
    """The public name of every write member comes from ``snake_to_camel`` alone."""

    assert schema.model_config["alias_generator"] is snake_to_camel
    for name, field in schema.model_fields.items():
        assert field.alias == snake_to_camel(name)


def test_example_update_keeps_missing_semantics_for_omitted_attributes() -> None:
    model = ExampleUpdate.model_validate({"title": "부분 수정"})

    assert model.model_fields_set == {"title"}
    # MISSING is installed through a `type: ignore[assignment]` default in app/schemas,
    # so the declared attribute types never overlap with the sentinel.
    assert model.description is MISSING  # type: ignore[comparison-overlap]
    assert model.status is MISSING  # type: ignore[comparison-overlap]
    assert model.score is MISSING  # type: ignore[comparison-overlap]


def test_example_update_rejects_loose_values_for_provided_attributes() -> None:
    with pytest.raises(ValidationError):
        ExampleUpdate.model_validate({"score": "50"})


def test_example_relationships_still_require_at_least_one_member() -> None:
    with pytest.raises(ValidationError, match="at least one example relationship is required"):
        ExampleRelationships.model_validate({})

    relationships = ExampleRelationships.model_validate(
        {"category": {"data": {"type": "exampleCategories", "id": "1"}}}
    )

    assert relationships.model_fields_set == {"category"}
    assert relationships.tags is MISSING  # type: ignore[comparison-overlap]

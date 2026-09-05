"""Declaration consistency tests for the explicitly composed application routes."""

from __future__ import annotations

import re
from typing import Any

from fastapi.routing import APIRoute

import config.routes as routes
from app.controllers.concerns import CrudActions

ComposedController = CrudActions[Any, Any, Any, Any]


def _composed_crud_controllers() -> list[ComposedController]:
    """Return every ``CrudActions`` instance bound as a module variable in ``config/routes.py``.

    ``config/routes.py`` deliberately composes each controller as a module-level variable
    before including its router, so the composed set can be inspected without importing
    the application. Inlining a controller into ``include_router(...)`` would make this
    collection silently empty; ``test_routes_expose_expected_crud_controllers`` guards that.
    """

    return [value for value in vars(routes).values() if isinstance(value, CrudActions)]


def test_routes_expose_expected_crud_controllers() -> None:
    composed = [type(controller).__name__ for controller in _composed_crud_controllers()]

    assert composed == [
        "ExamplesController",
        "ExampleCategoriesController",
        "ExampleTagsController",
    ]


def test_serializer_resource_path_matches_composed_prefix() -> None:
    for controller in _composed_crud_controllers():
        # ``crud_base._canonical_resource_location`` falls back to the router prefix without
        # complaining, so a mismatch ships wrong ``self`` links and ``Location`` headers.
        assert controller.serializer_class.resource_path == controller.prefix


def test_writable_relationship_aliases_exist_in_serializer() -> None:
    for controller in _composed_crud_controllers():
        # ``route_registrar`` skips relationship names it cannot find on the serializer, so a
        # typo in ``relationships_schema`` silently removes the write relationship routes.
        assert controller._writable_relationship_names <= set(controller.serializer_class.relationships)


def test_composed_type_names_are_lower_camel_case() -> None:
    for controller in _composed_crud_controllers():
        # Nothing validates ``type_name`` at runtime; it only decides the public contract.
        assert re.fullmatch(r"[a-z][a-zA-Z0-9]*", controller.serializer_class.type_name)


def test_reference_resource_controllers_are_read_only() -> None:
    read_only = {"ExampleCategoriesController", "ExampleTagsController"}

    for controller in _composed_crud_controllers():
        if type(controller).__name__ not in read_only:
            continue
        methods = {
            method for route in controller.router.routes if isinstance(route, APIRoute) for method in route.methods
        }
        assert methods == {"GET"}

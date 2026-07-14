"""Example JSON:API resource controller."""

from app.controllers.concerns import CrudActions
from app.models import Example
from app.schemas import (
    EXAMPLE_QUERY_POLICY,
    ExampleCreate,
    ExampleRelationships,
    ExampleReplace,
    ExampleUpdate,
)
from app.serializers import ExampleSerializer


class ExamplesController(CrudActions[Example, ExampleCreate, ExampleUpdate, ExampleReplace]):
    """Expose the example resource through inherited Rails-style actions."""

    model_class = Example
    serializer_class = ExampleSerializer
    create_schema = ExampleCreate
    update_schema = ExampleUpdate
    replace_schema = ExampleReplace
    relationships_schema = ExampleRelationships
    query_policy = EXAMPLE_QUERY_POLICY
    enable_upsert = True

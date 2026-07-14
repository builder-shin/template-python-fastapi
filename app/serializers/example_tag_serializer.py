"""JSON:API serializer declaration for example tags."""

from app.models import ExampleTag
from app.serializers.base import JsonApiSerializer


class ExampleTagSerializer(JsonApiSerializer[ExampleTag]):
    """Expose the public example-tag representation."""

    type_name = "exampleTags"
    resource_path = None
    attributes = ("name",)

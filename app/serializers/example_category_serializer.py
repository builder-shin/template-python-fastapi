"""JSON:API serializer declaration for example categories."""

from app.models import ExampleCategory
from app.serializers.base import JsonApiSerializer


class ExampleCategorySerializer(JsonApiSerializer[ExampleCategory]):
    """Expose the public example-category representation."""

    type_name = "exampleCategories"
    resource_path = "/api/v1/categories"
    attributes = ("name",)

"""JSON:API serializer declaration for examples."""

from types import MappingProxyType

from app.models import Example
from app.serializers.base import JsonApiSerializer, RelationshipDefinition
from app.serializers.example_category_serializer import ExampleCategorySerializer
from app.serializers.example_tag_serializer import ExampleTagSerializer


class ExampleSerializer(JsonApiSerializer[Example]):
    """Expose only the stable public example representation."""

    type_name = "examples"
    resource_path = "/api/v1/examples"
    attributes = ("title", "description", "status", "score", "created_at", "updated_at")
    relationships = MappingProxyType(
        {
            "category": RelationshipDefinition(
                attribute="category",
                serializer=ExampleCategorySerializer,
                many=False,
                linkage_attribute="category_id",
            ),
            "tags": RelationshipDefinition(
                attribute="tags",
                serializer=ExampleTagSerializer,
                many=True,
            ),
        }
    )

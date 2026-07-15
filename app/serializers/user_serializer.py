"""JSON:API serializer declaration for the authenticated user."""

from app.models import User
from app.serializers.base import JsonApiSerializer


class UserSerializer(JsonApiSerializer[User]):
    """Expose the public profile at the stable current-user endpoint."""

    type_name = "users"
    resource_path = "/api/v1/users/me"
    attributes = ("email", "is_active", "created_at", "updated_at")

    @classmethod
    def resource_location(cls, model: User) -> str:
        return "/api/v1/users/me"

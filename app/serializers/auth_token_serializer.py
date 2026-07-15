"""JSON:API serializer declaration for issued authentication tokens."""

from app.auth.tokens import AuthTokenResource
from app.serializers.base import JsonApiSerializer


class AuthTokenSerializer(JsonApiSerializer[AuthTokenResource]):
    """Expose only the issued token pair and fixed expiry metadata."""

    type_name = "authTokens"
    resource_path = None
    attributes = (
        "access_token",
        "refresh_token",
        "token_type",
        "expires_in",
        "refresh_expires_in",
    )

"""Explicit application route composition."""

from fastapi import APIRouter

from app.controllers.api.v1 import AuthController, ExamplesController, UsersController
from app.controllers.health_controller import HealthController

api_router = APIRouter()
auth_controller = AuthController(prefix="/api/v1/auth", tags=["authentication"])
examples_controller = ExamplesController(prefix="/api/v1/examples", tags=["examples"])
health_controller = HealthController(tags=["health"])
users_controller = UsersController(prefix="/api/v1/users", tags=["users"])
api_router.include_router(auth_controller.router)
api_router.include_router(examples_controller.router)
api_router.include_router(health_controller.router)
api_router.include_router(users_controller.router)

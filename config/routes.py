"""Explicit application route composition."""

from fastapi import APIRouter

from app.controllers.api.v1 import AuthController, ExamplesController

api_router = APIRouter()
auth_controller = AuthController(prefix="/api/v1/auth", tags=["authentication"])
examples_controller = ExamplesController(prefix="/api/v1/examples", tags=["examples"])
api_router.include_router(auth_controller.router)
api_router.include_router(examples_controller.router)

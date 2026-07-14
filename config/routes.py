"""Explicit application route composition."""

from fastapi import APIRouter

from app.controllers.api.v1 import ExamplesController

api_router = APIRouter()
examples_controller = ExamplesController(prefix="/api/v1/examples", tags=["examples"])
api_router.include_router(examples_controller.router)

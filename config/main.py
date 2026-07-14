"""FastAPI application factory."""

from fastapi import FastAPI

from app.jsonapi import register_exception_handlers
from config.routes import api_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="FastAPI Template",
        version="0.1.0",
        docs_url="/api-docs",
        redoc_url=None,
        openapi_url="/api/schema",
        swagger_ui_oauth2_redirect_url=None,
    )
    register_exception_handlers(app)
    app.include_router(api_router)
    return app

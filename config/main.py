"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from app.jsonapi import register_exception_handlers
from config.auth import AuthSettings
from config.database import DatabaseSettings, build_engine
from config.routes import api_router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Dispose the application engine when the process shuts down."""

    try:
        yield
    finally:
        app.state.engine.dispose()


def create_app() -> FastAPI:
    auth_settings = AuthSettings.from_env()
    database_settings = DatabaseSettings.from_env()
    engine = build_engine(database_settings)
    app = FastAPI(
        title="FastAPI Template",
        version="0.1.0",
        docs_url="/api-docs",
        redoc_url=None,
        openapi_url="/api/schema",
        swagger_ui_oauth2_redirect_url=None,
        lifespan=_lifespan,
    )
    app.state.auth_settings = auth_settings
    app.state.database_settings = database_settings
    app.state.engine = engine
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    register_exception_handlers(app)
    app.include_router(api_router)
    return app

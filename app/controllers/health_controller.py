"""Process liveness and PostgreSQL readiness endpoints."""

from __future__ import annotations

from enum import Enum
from typing import cast

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.controllers.concerns.jsonapi_routes import JsonApiRoute
from app.jsonapi import ErrorDocument, JsonApiException, JsonApiResponse, SuccessDocument
from config.database import get_session

_SESSION_DEPENDENCY = Depends(get_session)
_HEALTH_DOCUMENT = SuccessDocument(data=None, meta={"status": "ok"})


class HealthController:
    """Expose health checks without JSON:API Accept negotiation."""

    def __init__(self, *, tags: list[str]) -> None:
        self.router = APIRouter(
            tags=cast(list[str | Enum], tags),
            route_class=JsonApiRoute,
        )
        self.router.add_api_route(
            "/health/live",
            self.live,
            methods=["GET"],
            response_class=JsonApiResponse,
            response_model=SuccessDocument,
            name="HealthController.live",
        )
        self.router.add_api_route(
            "/health/ready",
            self.ready,
            methods=["GET"],
            response_class=JsonApiResponse,
            response_model=SuccessDocument,
            responses={
                503: {
                    "description": "JSON:API health check error",
                    "model": ErrorDocument,
                }
            },
            name="HealthController.ready",
        )

    def live(self) -> JsonApiResponse:
        """Return process liveness without resolving a database session."""

        return JsonApiResponse(_HEALTH_DOCUMENT)

    def ready(self, session: Session = _SESSION_DEPENDENCY) -> JsonApiResponse:
        """Return readiness when PostgreSQL accepts a trivial query."""

        try:
            session.execute(select(1))
        except SQLAlchemyError:
            raise JsonApiException(
                status_code=503,
                code="INTERNAL_SERVER_ERROR",
            ) from None
        return JsonApiResponse(_HEALTH_DOCUMENT)

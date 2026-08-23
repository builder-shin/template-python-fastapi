"""Process liveness and PostgreSQL readiness endpoints."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.controllers.concerns import JsonApiController
from app.jsonapi import JsonApiException, JsonApiResponse, SuccessDocument, jsonapi_error_responses
from config.database import get_session

_SESSION_DEPENDENCY = Depends(get_session)
_HEALTH_DOCUMENT = SuccessDocument(data=None, meta={"status": "ok"})


class HealthController(JsonApiController):
    """Expose health checks without JSON:API Accept negotiation.

    Probes are polled by orchestrators that send no ``Accept`` header, so this
    controller opts out of the shared negotiation with ``negotiate_accept``
    instead of hand-assembling a router that merely happens to omit it. Probe
    paths are absolute (``/health/live``, ``/health/ready``) rather than versioned
    under a resource prefix, so it also declares ``allow_root_prefix``.
    """

    negotiate_accept = False
    allow_root_prefix = True

    def __init__(self, *, tags: list[str]) -> None:
        super().__init__(tags=tags)
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
            responses=jsonapi_error_responses(503),
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

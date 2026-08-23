"""Shared JSON:API controller base assembly contract tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import Body, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.controllers.api.v1 import AuthController, ExamplesController, UsersController
from app.controllers.concerns import JsonApiController
from app.controllers.concerns.jsonapi_routes import JsonApiRoute
from app.controllers.health_controller import HealthController
from app.jsonapi import JSONAPI_MEDIA_TYPE, register_exception_handlers, require_jsonapi_accept


class ProbeDocument(BaseModel):
    """Minimal write body so ``JsonApiRoute`` has a media type to validate."""

    value: str


_PROBE_BODY = Body(..., media_type=JSONAPI_MEDIA_TYPE)


class ProbeController(JsonApiController):
    """Default controller shape: prefixed, negotiated, JSON:API route class."""

    def __init__(self, *, prefix: str = "/probe", tags: list[str] | None = None) -> None:
        super().__init__(prefix=prefix, tags=tags if tags is not None else ["probe"])
        self.router.add_api_route("/items", self.index, methods=["GET"], name="ProbeController.index")
        self.router.add_api_route("/items", self.create, methods=["POST"], name="ProbeController.create")

    def index(self) -> dict[str, str]:
        return {"status": "ok"}

    def create(self, document: ProbeDocument = _PROBE_BODY) -> dict[str, str]:
        return {"status": document.value}


class UnnegotiatedProbeController(ProbeController):
    """Deliberate opt-out from ``Accept`` negotiation, the HealthController shape."""

    negotiate_accept = False


class RootProbeController(JsonApiController):
    """Deliberate opt-out from prefixing, the HealthController shape."""

    allow_root_prefix = True


def _client(controller: JsonApiController) -> Iterator[TestClient]:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(controller.router)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def negotiated_client() -> Iterator[TestClient]:
    yield from _client(ProbeController())


@pytest.fixture
def unnegotiated_client() -> Iterator[TestClient]:
    yield from _client(UnnegotiatedProbeController())


@pytest.mark.parametrize("prefix", ["api/v1/probe", "/api/v1/probe/", "probe", "", "/"])
def test_malformed_prefix_is_rejected(prefix: str) -> None:
    with pytest.raises(ValueError, match="must start with"):
        ProbeController(prefix=prefix)


def test_default_router_declares_accept_negotiation_and_the_jsonapi_route_class() -> None:
    controller = ProbeController(prefix="/probe")

    assert controller.prefix == "/probe"
    assert controller.router.prefix == "/probe"
    assert controller.router.route_class is JsonApiRoute
    assert require_jsonapi_accept in [dependency.dependency for dependency in controller.router.dependencies]


def test_empty_prefix_is_allowed_only_for_a_controller_that_declares_it() -> None:
    controller = RootProbeController(tags=["probe"])

    assert controller.prefix == ""
    assert controller.router.prefix == ""
    assert controller.router.route_class is JsonApiRoute


@pytest.mark.parametrize("prefix", ["", "/"])
def test_root_prefix_is_rejected_without_the_declared_opt_out(prefix: str) -> None:
    """A controller that reaches the root by omission must fail as loudly as a typo."""

    with pytest.raises(ValueError, match="route prefix must start with '/' and must not end with '/'"):
        JsonApiController(prefix=prefix, tags=["probe"])


@pytest.mark.parametrize("controller_class", [AuthController, ExamplesController, UsersController])
def test_shipped_controllers_reject_a_root_prefix(
    controller_class: type[AuthController] | type[ExamplesController] | type[UsersController],
) -> None:
    """None of the mounted controllers may reach the application root by a blank prefix."""

    with pytest.raises(ValueError, match="route prefix must start with '/' and must not end with '/'"):
        controller_class(prefix="", tags=["probe"])


def test_health_controller_is_the_only_declared_root_controller() -> None:
    assert HealthController.allow_root_prefix is True
    assert JsonApiController.allow_root_prefix is False
    assert HealthController(tags=["health"]).prefix == ""


def test_negotiate_accept_false_omits_only_the_accept_dependency() -> None:
    controller = UnnegotiatedProbeController()

    assert controller.router.dependencies == []
    assert controller.router.route_class is JsonApiRoute


def test_negotiated_controller_rejects_a_non_jsonapi_accept_header(negotiated_client: TestClient) -> None:
    response = negotiated_client.get("/probe/items", headers={"Accept": "text/html"})

    assert response.status_code == 406
    assert response.json()["errors"][0]["code"] == "NOT_ACCEPTABLE"


def test_opted_out_controller_serves_a_non_jsonapi_accept_header(unnegotiated_client: TestClient) -> None:
    response = unnegotiated_client.get("/probe/items", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_opted_out_controller_still_validates_the_write_content_type(unnegotiated_client: TestClient) -> None:
    response = unnegotiated_client.post(
        "/probe/items",
        json={"value": "written"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 415
    assert response.json()["errors"][0]["code"] == "UNSUPPORTED_MEDIA_TYPE"

    accepted = unnegotiated_client.post(
        "/probe/items",
        content=b'{"value": "written"}',
        headers={"Content-Type": JSONAPI_MEDIA_TYPE},
    )

    assert accepted.status_code == 200
    assert accepted.json() == {"status": "written"}

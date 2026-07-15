"""Docker Compose worker topology contract tests."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _compose_config(environment: dict[str, str] | None = None) -> dict[str, Any]:
    command_environment = os.environ.copy()
    command_environment.update(environment or {})
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        env=command_environment,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return cast(dict[str, Any], json.loads(result.stdout))


def test_compose_defines_database_redis_migration_api_and_worker() -> None:
    config = _compose_config()

    assert set(config["services"]) == {"db", "redis", "migrate", "api", "worker"}
    assert set(config["volumes"]) == {"postgres_data", "redis_data"}

    redis = config["services"]["redis"]
    assert redis["healthcheck"]["test"] == ["CMD", "redis-cli", "ping"]
    assert {(volume["source"], volume["target"]) for volume in redis["volumes"]} == {("redis_data", "/data")}

    migrate = config["services"]["migrate"]
    assert migrate["command"] == ["alembic", "upgrade", "head"]

    worker = config["services"]["worker"]
    assert worker["command"] == ["dramatiq", "app.jobs"]
    assert worker["healthcheck"] == {"disable": True}
    assert {service: dependency["condition"] for service, dependency in worker["depends_on"].items()} == {
        "migrate": "service_completed_successfully",
        "redis": "service_healthy",
    }
    assert worker["environment"]["DATABASE_URL"].startswith("postgresql+psycopg://")
    assert worker["environment"]["REDIS_URL"] == "redis://redis:6379/0"


def test_api_receives_auth_and_broker_settings_without_redis_dependency() -> None:
    api = _compose_config()["services"]["api"]

    assert {service: dependency["condition"] for service, dependency in api["depends_on"].items()} == {
        "migrate": "service_completed_successfully"
    }
    assert api["environment"]["REDIS_URL"] == "redis://redis:6379/0"
    assert len(api["environment"]["JWT_SECRET_KEY"].encode("utf-8")) >= 32
    assert api["environment"]["JWT_ISSUER"] == "template-python-fastapi"
    assert api["environment"]["JWT_AUDIENCE"] == "template-python-fastapi"
    assert api["environment"]["JWT_ACCESS_EXPIRES_SECONDS"] == "900"
    assert api["environment"]["JWT_REFRESH_EXPIRES_SECONDS"] == "2592000"
    assert api["environment"]["JWT_LEEWAY_SECONDS"] == "0"


def test_host_redis_url_does_not_override_the_compose_network_endpoint() -> None:
    config = _compose_config({"REDIS_URL": "redis://localhost:6379/0"})

    assert config["services"]["api"]["environment"]["REDIS_URL"] == "redis://redis:6379/0"
    assert config["services"]["worker"]["environment"]["REDIS_URL"] == "redis://redis:6379/0"


def test_example_environment_labels_a_long_development_only_secret() -> None:
    example = (PROJECT_ROOT / ".env.example").read_text()
    values = {
        key: value
        for line in example.splitlines()
        if line and not line.startswith("#")
        for key, value in [line.split("=", maxsplit=1)]
    }

    assert "development-only" in example.lower()
    assert len(values["JWT_SECRET_KEY"].encode("utf-8")) >= 32
    assert values["JWT_ISSUER"] == "template-python-fastapi"
    assert values["JWT_AUDIENCE"] == "template-python-fastapi"
    assert values["JWT_ACCESS_EXPIRES_SECONDS"] == "900"
    assert values["JWT_REFRESH_EXPIRES_SECONDS"] == "2592000"
    assert values["JWT_LEEWAY_SECONDS"] == "0"
    assert values["REDIS_URL"] == "redis://localhost:6379/0"


def test_runtime_healthcheck_uses_database_readiness_endpoint() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text()

    healthcheck = dockerfile.split("HEALTHCHECK", maxsplit=1)[1]
    assert "/health/ready" in healthcheck
    assert "/api/schema" not in healthcheck

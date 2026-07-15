"""README and continuous-integration contract tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
README = (PROJECT_ROOT / "README.md").read_text()
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


def test_readme_documents_fail_closed_jwt_secrets() -> None:
    assert "JWT_SECRET_KEY" in README
    assert "개발 전용" in README
    assert "운영" in README
    assert "32바이트" in README
    assert "암묵적인 기본값" in README


def test_readme_contains_jsonapi_authentication_curl_examples() -> None:
    for path in (
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/users/me",
    ):
        assert path in README

    for resource_type in ("users", "authCredentials"):
        assert f"'{resource_type}'" in README
    assert '"type": "refreshTokens"' in README

    assert README.count("Accept: application/vnd.api+json") >= 6
    assert README.count("Content-Type: application/vnd.api+json") >= 5
    assert "Authorization: Bearer ${ACCESS_TOKEN}" in README


def test_readme_authentication_flow_uses_private_shell_variables() -> None:
    assert "read -r -s" in README
    assert "AUTH_PASSWORD='...'" not in README
    assert 'AUTH_RESPONSE="$(' in README
    assert 'REFRESH_RESPONSE="$(' in README
    assert 'ACCESS_TOKEN="$(printf' in README
    assert 'REFRESH_TOKEN="$(printf' in README
    assert 'json.load(sys.stdin)["data"]["attributes"]' in README


def test_readme_sends_password_and_refresh_token_through_encoded_stdin() -> None:
    assert "jsonapi_credentials_document()" in README
    assert "jsonapi_refresh_document()" in README
    assert 'sys.stdin.buffer.read().split(b"\\0")' in README
    assert "json.dumps(" in README
    assert README.count("--data-binary @-") >= 4
    assert "printf '%s\\0%s\\0%s\\0' 'users' 'person@example.com' \"${AUTH_PASSWORD}\"" in README
    assert "printf '%s\\0%s\\0%s\\0' 'authCredentials' 'person@example.com' \"${AUTH_PASSWORD}\"" in README
    assert "printf '%s\\0' \"${REFRESH_TOKEN}\"" in README
    assert '--data "{\\"data\\"' not in README


def test_readme_documents_token_storage_and_logout_limit() -> None:
    assert "refresh token" in README
    assert "안전하게 보관" in README
    assert "cookie" in README
    assert "JSON body" in README
    assert "logout" in README
    assert "최대 15분" in README


def test_readme_documents_protected_example_write() -> None:
    assert "Authorization: Bearer ${ACCESS_TOKEN}" in README
    assert '"type":"examples"' in README
    assert "/api/v1/examples" in README
    assert "Example 읽기" in README
    assert "Example 쓰기" in README


def test_readme_documents_manual_dramatiq_workflow() -> None:
    assert "dramatiq app.jobs" in README
    assert "process_example.send(str(example.id))" in README
    assert "자동 enqueue" in README
    assert "docker compose ps redis worker" in README
    assert "Redis" in README
    assert "worker" in README


def test_readme_documents_full_verification_commands() -> None:
    verification = README.split("## 검증", maxsplit=1)[1]

    for command in (
        "uv sync --frozen",
        "./scripts/check.sh",
        "docker compose config --quiet",
        "docker build --target runtime",
        "docker compose up -d --build --wait",
        "docker compose down -v",
    ):
        assert command in verification
    assert "\nuv sync\n" not in verification


def test_ci_runs_frozen_checks_compose_validation_and_production_build() -> None:
    assert WORKFLOW_PATH.is_file()
    workflow_text = WORKFLOW_PATH.read_text()
    workflow = cast(
        dict[str, Any],
        yaml.load(workflow_text, Loader=yaml.BaseLoader),  # Repository-owned static YAML.
    )

    assert set(workflow["on"]) == {"push", "pull_request"}
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert set(jobs) == {"checks"}
    assert jobs["checks"]["runs-on"] == "ubuntu-latest"
    steps = jobs["checks"]["steps"]
    uses = [step["uses"] for step in steps if "uses" in step]
    commands = [step["run"].strip() for step in steps if "run" in step]

    assert any(action.startswith("actions/checkout@") for action in uses)
    assert any(action.startswith("astral-sh/setup-uv@") for action in uses)
    assert commands == [
        "uv sync --frozen",
        "./scripts/check.sh",
        "docker compose config --quiet",
        "docker build --target runtime --tag template-python-fastapi:ci .",
    ]
    assert "sqlite" not in workflow_text.casefold()

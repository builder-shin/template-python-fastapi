"""README and continuous-integration contract tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

from app.controllers.concerns.crud_base import CrudDeclarations

PROJECT_ROOT = Path(__file__).resolve().parents[2]
README = (PROJECT_ROOT / "README.md").read_text()
AGENTS = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


def _first_bash_block(document: str, heading: str) -> list[str]:
    section = document.split(heading, maxsplit=1)[1]
    body = section.split("```bash", maxsplit=1)[1].split("```", maxsplit=1)[0]
    return [line.strip() for line in body.strip().splitlines() if line.strip()]


def test_readme_documents_fail_closed_jwt_secrets() -> None:
    assert "JWT_SECRET_KEY" in README
    assert "개발 전용" in README
    assert "운영" in README
    assert "32바이트" in README
    assert "암묵적인 기본값" in README


def test_readme_documents_fail_closed_required_environment_variables() -> None:
    required = README.split("## 필수 환경 변수", maxsplit=1)[1]

    for variable in ("DATABASE_URL", "REDIS_URL", "JWT_SECRET_KEY"):
        assert variable in required
    assert "암묵적인 기본값이 없습니다" in required
    assert "DATABASE_URL is required" in required
    assert "DB_POOL_SIZE must be an integer" in required


def test_readme_local_process_quickstart_exports_the_fail_closed_variables() -> None:
    commands = _first_bash_block(README, "## 로컬 프로세스로 실행")

    # DATABASE_URL and JWT_SECRET_KEY are fail-closed in config/, so the quickstart
    # cannot rely on implicit defaults from .env.example any more.
    assert any(command.startswith("export DATABASE_URL=") for command in commands)
    assert any(command.startswith("export JWT_SECRET_KEY=") for command in commands)
    assert commands.index(next(c for c in commands if c.startswith("export DATABASE_URL="))) < commands.index(
        "uv run alembic upgrade head"
    )
    section = README.split("## 로컬 프로세스로 실행", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]
    assert "다른 값을 사용할 때는 `DATABASE_URL`" not in section
    assert "기본값이 없으므로" in section


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


def test_readme_documents_the_refresh_session_retention_job() -> None:
    assert "purge_expired_refresh_sessions.send()" in README
    assert "REFRESH_SESSION_RETENTION_SECONDS" in README
    assert "`604800`초(7일)" in README
    assert "REFRESH_SESSION_RETENTION_SECONDS must be non-negative" in README
    assert "expires_at" in README
    assert "TOKEN_EXPIRED" in README
    assert "INVALID_TOKEN" in README
    # The runtime image has no uv (Dockerfile copies it only into the builder stage),
    # so the recipe must use the image's own interpreter from the compose project directory.
    assert (
        "0 * * * * cd /path/to/project && docker compose exec -T api python -c "
        '"from app.jobs import purge_expired_refresh_sessions; purge_expired_refresh_sessions.send()"'
    ) in README
    assert "cd /app && uv run python -c" not in README


def test_readme_documents_the_new_resource_procedure() -> None:
    section = README.split("## 새 리소스 추가", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]

    for wiring_point in (
        "app/models/__init__.py",
        "db/migrations/versions/",
        "app/schemas/__init__.py",
        "app/serializers/__init__.py",
        "app/controllers/api/v1/__init__.py",
        "config/routes.py",
        "tests/test_example_controller.py",
    ):
        assert wiring_point in section
    for naming_rule in ("type_name", "resource_path", "relationships_schema"):
        assert naming_rule in section
    for command in (
        "uv run alembic revision",
        "uv run pytest --no-cov tests/controllers",
        "uv run pytest --no-cov tests/config tests/integration",
        "./scripts/check.sh",
    ):
        assert command in section
    assert "tests/config/test_routes.py" in section
    assert "app/AGENTS.md" in section


def test_readme_new_resource_recipe_separates_required_from_conditional_declarations() -> None:
    section = README.split("## 새 리소스 추가", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]

    # `relationships_schema` carries a default on the base, so a controller without it is a
    # supported configuration; the genuinely required names are bare annotations.
    assert CrudDeclarations.relationships_schema is None
    assert "`relationships_schema`는 쓰기 가능한 관계가 있을 때" in section
    assert "`relationships_schema`, `query_policy`가 필수" not in section


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


def test_readme_and_agents_verification_commands_match() -> None:
    readme_commands = _first_bash_block(README, "## 검증")
    agents_commands = _first_bash_block(AGENTS, "## 검증 명령")

    assert readme_commands == agents_commands
    assert readme_commands == [
        "uv sync --frozen",
        "./scripts/check.sh",
        "docker compose config --quiet",
        "docker build --target runtime --tag template-python-fastapi:verify .",
        "docker compose up -d --build --wait",
        "docker compose down -v",
    ]


def test_readme_documents_task_runner_entrypoints() -> None:
    verification = README.split("## 검증", maxsplit=1)[1]

    for task in (
        "uv run poe lint",
        "uv run poe format",
        "uv run poe typecheck",
        "uv run poe test",
        "uv run poe seed",
        "uv run poe check",
    ):
        assert task in verification
    assert "[tool.poe.tasks]" in verification
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

#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
api_root="$(cd "$script_dir/.." && pwd)"
compose_file="$api_root/docker-compose.test.yml"
test_project_name="template-python-fastapi-test-$$-$RANDOM"
compose_args=(-f "$compose_file" -p "$test_project_name")
started_test_database=false
created_coverage_file=false

if [[ -z "${COVERAGE_FILE:-}" ]]; then
  export COVERAGE_FILE="$api_root/.coverage.$test_project_name"
  created_coverage_file=true
fi

cleanup() {
  if [[ "$started_test_database" == true ]]; then
    docker compose "${compose_args[@]}" down -v
  fi
  if [[ "$created_coverage_file" == true ]]; then
    rm -f "$COVERAGE_FILE"
  fi
}

trap cleanup EXIT
cd "$api_root"

if [[ -z "${TEST_DATABASE_URL:-}" ]]; then
  started_test_database=true
  TEST_DB_PORT=0 docker compose "${compose_args[@]}" up -d --wait db
  test_database_endpoint="$(docker compose "${compose_args[@]}" port db 5432)"
  test_database_port="${test_database_endpoint##*:}"
  export TEST_DATABASE_URL="postgresql+psycopg://fastapi:fastapi@127.0.0.1:${test_database_port}/fastapi_template_test" # pragma: allowlist secret
fi

uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest
uv run pre-commit run detect-secrets --all-files

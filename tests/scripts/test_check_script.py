"""Self-contained backend check script tests."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]


def test_pre_commit_hooks_target_standalone_repository_root() -> None:
    config = (API_ROOT / ".pre-commit-config.yaml").read_text()

    assert "files: ^apps/api/" not in config
    assert 'args: ["--baseline", ".secrets.baseline"]' in config


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents)
    path.chmod(0o755)


def test_check_script_isolates_its_compose_project_and_host_port(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_path = tmp_path / "commands.log"
    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'docker:%s|port=%s\\n' "$*" "${TEST_DB_PORT:-}" >> "$CHECK_LOG"
if [[ "$*" == *" port db 5432" ]]; then
  printf '127.0.0.1:49123\\n'
fi
""",
    )
    _write_executable(
        fake_bin / "uv",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'uv:%s|url=%s|coverage=%s\\n' "$*" "${TEST_DATABASE_URL:-}" "${COVERAGE_FILE:-}" >> "$CHECK_LOG"
""",
    )
    environment = os.environ.copy()
    environment.pop("TEST_DATABASE_URL", None)
    environment["CHECK_LOG"] = str(log_path)
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"

    result = subprocess.run(
        [str(API_ROOT / "scripts/check.sh")],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    lines = log_path.read_text().splitlines()
    up_line = next(line for line in lines if " up -d --wait db" in line)
    down_line = next(line for line in lines if " down -v" in line)
    project_match = re.search(r" -p (template-python-fastapi-test-[^ ]+)", up_line)
    assert project_match is not None
    assert f" -p {project_match.group(1)}" in down_line
    assert up_line.endswith("|port=0")
    expected_pytest_prefix = "uv:run pytest|url=postgresql+psycopg://fastapi:fastapi@127.0.0.1:49123/fastapi_template_test|coverage="  # pragma: allowlist secret
    assert any(line.startswith(expected_pytest_prefix) for line in lines)
    pytest_line = next(line for line in lines if line.startswith("uv:run pytest|"))
    assert re.search(r"\|coverage=.*/\.coverage\.template-python-fastapi-test-[^|]+$", pytest_line)

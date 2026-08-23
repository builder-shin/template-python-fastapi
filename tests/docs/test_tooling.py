"""Static-analysis and task-runner configuration contract tests."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, cast

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
PRE_COMMIT_PATH = PROJECT_ROOT / ".pre-commit-config.yaml"


def _mypy_config() -> dict[str, Any]:
    return cast(dict[str, Any], PYPROJECT["tool"]["mypy"])


def _poe_tasks() -> dict[str, Any]:
    return cast(dict[str, Any], PYPROJECT["tool"]["poe"]["tasks"])


def _task_command(task: object) -> str:
    if isinstance(task, str):
        return task
    assert isinstance(task, dict)
    return cast(str, task["shell"])


def test_mypy_type_checks_the_tests_package() -> None:
    mypy = _mypy_config()

    assert mypy["strict"] is True
    assert mypy["exclude"] == [".venv/"]
    assert "tests/" not in mypy["exclude"]


def test_mypy_resolves_db_namespace_package_explicitly() -> None:
    mypy = _mypy_config()

    # `db/` stays a namespace package so `python -m db.seeds` and the hatch wheel
    # packages keep working; without these two settings mypy aborts with
    # "Source file found twice under different module names" once tests/ is checked.
    assert (PROJECT_ROOT / "db" / "seeds.py").is_file()
    assert (PROJECT_ROOT / "db" / "__init__.py").exists() is False
    assert mypy["explicit_package_bases"] is True
    assert mypy["mypy_path"] == "."


def test_dev_dependency_group_declares_type_stub_and_task_runner() -> None:
    declared = {
        requirement.split(">=")[0].split("==")[0].strip() for requirement in PYPROJECT["dependency-groups"]["dev"]
    }

    for package in ("poethepoet", "pyyaml", "types-pyyaml"):
        assert package in declared
    for package in ("detect-secrets", "httpx", "mypy", "pre-commit", "pytest", "pytest-cov", "ruff"):
        assert package in declared


def test_poe_tasks_expose_lint_typecheck_test_check_and_seed() -> None:
    tasks = _poe_tasks()

    required = {
        "lint",
        "format",
        "format-check",
        "typecheck",
        "test",
        "test-jsonapi",
        "test-controllers",
        "test-db",
        "migrate",
        "seed",
        "db-up",
        "check",
    }
    assert required <= set(tasks)
    assert tasks["lint"] == "ruff check ."
    assert tasks["typecheck"] == "mypy ."
    assert tasks["test"] == "pytest"
    assert tasks["seed"] == "python -m db.seeds"
    assert tasks["migrate"] == "alembic upgrade head"


def test_poe_check_delegates_to_the_shell_gate_without_reimplementing_it() -> None:
    check = _task_command(_poe_tasks()["check"])

    assert check == "./scripts/check.sh"
    assert "docker compose" not in check
    assert (PROJECT_ROOT / "scripts" / "check.sh").is_file()


def test_only_the_full_gate_needs_a_posix_shell() -> None:
    # poe's default shell interpreter is POSIX, so a `shell = ...` task cannot run on
    # Windows without Git Bash or WSL. AGENTS.md and README.md both promise that only
    # `check` carries that requirement, so no other task may declare a shell command.
    shell_tasks = {name for name, task in _poe_tasks().items() if isinstance(task, dict) and "shell" in task}

    assert shell_tasks == {"check"}
    for name in ("db-up", "worker", "compose-verify"):
        command = _poe_tasks()[name]
        assert isinstance(command, str)
        # A plain string is only safe without an interpreter when it needs no shell syntax.
        assert not any(token in command for token in ("|", ">", "<", "&&", ";"))
    assert _poe_tasks()["db-up"] == "docker compose up -d --wait db"
    assert _poe_tasks()["worker"] == "docker compose up -d --wait worker"
    assert _poe_tasks()["compose-verify"] == "docker compose config --quiet"


def test_pre_commit_type_checks_with_the_project_environment() -> None:
    config = cast(
        dict[str, Any],
        yaml.load(PRE_COMMIT_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader),
    )
    repos = config["repos"]
    sources = [repo["repo"] for repo in repos]

    assert any(source.endswith("astral-sh/ruff-pre-commit") for source in sources)
    assert any(source.endswith("Yelp/detect-secrets") for source in sources)

    local_hooks = [hook for repo in repos if repo["repo"] == "local" for hook in repo["hooks"]]
    mypy_hook = next(hook for hook in local_hooks if hook["id"] == "mypy")

    # mirrors-mypy builds an isolated environment that cannot see the SQLAlchemy and
    # Pydantic plugins, so it would report a different error set than scripts/check.sh.
    assert mypy_hook["language"] == "system"
    assert "uv run mypy" in mypy_hook["entry"]
    assert mypy_hook["pass_filenames"] == "false"

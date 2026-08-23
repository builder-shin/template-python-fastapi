"""Layer AGENTS.md guide contract tests.

These guides are the entry point an agent reads before touching a layer, so a stale
symbol name or file count sends the reader to a `grep` with zero hits. Each assertion
below is pinned against the tree the guide describes rather than against prose.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONCERNS_GUIDE = (PROJECT_ROOT / "app" / "controllers" / "concerns" / "AGENTS.md").read_text(encoding="utf-8")
CONTROLLER_TESTS_GUIDE = (PROJECT_ROOT / "tests" / "controllers" / "AGENTS.md").read_text(encoding="utf-8")
JSONAPI_GUIDE = (PROJECT_ROOT / "app" / "jsonapi" / "AGENTS.md").read_text(encoding="utf-8")


def test_concerns_guide_names_the_query_parameter_rejection_helper_that_exists() -> None:
    document_parsing = (PROJECT_ROOT / "app" / "controllers" / "concerns" / "document_parsing.py").read_text(
        encoding="utf-8"
    )

    # The refactor moved the private staticmethod to a public module-level function.
    assert "def reject_query_parameters(" in document_parsing
    assert "document_parsing.reject_query_parameters" in CONCERNS_GUIDE
    assert "_reject_query_parameters" not in CONCERNS_GUIDE


def test_controller_test_guide_counts_every_file_in_its_directory() -> None:
    files = sorted(path.name for path in (PROJECT_ROOT / "tests" / "controllers").glob("test_*.py"))

    assert files == [
        "test_crud_actions.py",
        "test_jsonapi_controller.py",
        "test_relationship_actions.py",
        "test_upsert.py",
    ]
    # Korean numeral for the file count; the guide must not keep claiming three.
    assert "이 디렉터리의 네 파일" in CONTROLLER_TESTS_GUIDE
    for name in files:
        assert f"`{name}`" in CONTROLLER_TESTS_GUIDE
        assert f"`uv run pytest --no-cov tests/controllers/{name} -q`" in CONCERNS_GUIDE


def test_jsonapi_guide_records_the_pydantic_core_float_rendering_decision() -> None:
    assert "model_dump_json(by_alias=True, exclude_none=True)" in JSONAPI_GUIDE
    assert "pydantic-core" in JSONAPI_GUIDE
    assert "0.00001" in JSONAPI_GUIDE

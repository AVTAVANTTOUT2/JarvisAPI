"""Tests du générateur automatique de tests manquants."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_list_public_functions_skips_private(tmp_path):
    from scripts.test_coverage_scan import list_public_functions

    (tmp_path / "m.py").write_text(
        "def public_one():\n    return 1\n\n"
        "def _private():\n    return 2\n\n"
        "async def public_async():\n    return 3\n",
        encoding="utf-8",
    )
    fns = list_public_functions(tmp_path / "m.py")
    names = {f.name for f in fns}
    assert names == {"public_one", "public_async"}
    assert next(f for f in fns if f.name == "public_async").is_async is True


def test_list_public_functions_invalid_syntax_returns_empty(tmp_path):
    from scripts.test_coverage_scan import list_public_functions

    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    assert list_public_functions(tmp_path / "broken.py") == []


def test_find_uncovered_functions_detects_missing_reference(tmp_path):
    from scripts.test_coverage_scan import find_uncovered_functions

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "m.py").write_text(
        "def covered():\n    return 1\n\ndef uncovered():\n    return 2\n", encoding="utf-8",
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_m.py").write_text("from src.m import covered\n", encoding="utf-8")

    uncovered = find_uncovered_functions([src_dir], tests_dir)
    assert [f.name for f in uncovered] == ["uncovered"]


def test_find_uncovered_ignores_test_files_themselves(tmp_path):
    from scripts.test_coverage_scan import find_uncovered_functions

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "test_helper.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    assert find_uncovered_functions([src_dir], tests_dir) == []


@pytest.mark.asyncio
async def test_generate_test_writes_and_validates(tmp_path):
    from scripts.test_coverage_scan import FunctionInfo, generate_test_for_function

    (tmp_path / "m.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    fn = FunctionInfo(name="add", lineno=1, end_lineno=2, is_async=False, module_path=tmp_path / "m.py")

    fake_test_code = (
        "import sys\nsys.path.insert(0, '.')\nfrom m import add\n\n"
        "def test_add():\n    assert add(2, 3) == 5\n"
    )
    fake_response = {"content": fake_test_code, "tokens_total": 10}

    with patch("llm.chat", new=AsyncMock(return_value=fake_response)):
        result = await generate_test_for_function(fn, base_dir=tmp_path)

    assert result["ok"] is True
    assert Path(result["path"]).exists()
    assert Path(result["path"]).read_text(encoding="utf-8").strip() == fake_test_code.strip()


@pytest.mark.asyncio
async def test_generate_test_discards_failing_test(tmp_path):
    from scripts.test_coverage_scan import FunctionInfo, generate_test_for_function

    (tmp_path / "m.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    fn = FunctionInfo(name="add", lineno=1, end_lineno=2, is_async=False, module_path=tmp_path / "m.py")

    broken_test_code = (
        "import sys\nsys.path.insert(0, '.')\nfrom m import add\n\n"
        "def test_add_wrong():\n    assert add(2, 3) == 999\n"
    )
    fake_response = {"content": broken_test_code, "tokens_total": 10}

    with patch("llm.chat", new=AsyncMock(return_value=fake_response)):
        result = await generate_test_for_function(fn, base_dir=tmp_path)

    assert result["ok"] is False
    # le fichier a été jeté, aucune trace
    generated_files = list((tmp_path / "tests" / "generated").glob("*.py")) if (tmp_path / "tests").exists() else []
    assert generated_files == []


@pytest.mark.asyncio
async def test_generate_test_llm_failure_is_reported(tmp_path):
    from scripts.test_coverage_scan import FunctionInfo, generate_test_for_function

    (tmp_path / "m.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    fn = FunctionInfo(name="add", lineno=1, end_lineno=2, is_async=False, module_path=tmp_path / "m.py")

    with patch("llm.chat", new=AsyncMock(side_effect=RuntimeError("down"))):
        result = await generate_test_for_function(fn, base_dir=tmp_path)
    assert result["ok"] is False
    assert "indisponible" in result["reason"]


@pytest.mark.asyncio
async def test_run_test_generation_disabled_by_default(monkeypatch):
    from scripts.test_coverage_scan import run_test_generation

    monkeypatch.setattr("config.AUTO_TEST_GEN_ENABLED", False)
    result = await run_test_generation()
    assert result == {"ok": False, "reason": "AUTO_TEST_GEN_ENABLED désactivé"}


@pytest.mark.asyncio
async def test_run_test_generation_requires_target_dirs(monkeypatch):
    from scripts.test_coverage_scan import run_test_generation

    monkeypatch.setattr("config.AUTO_TEST_GEN_ENABLED", True)
    monkeypatch.setattr("config.AUTO_TEST_GEN_TARGET_DIRS", "")
    result = await run_test_generation()
    assert result["ok"] is False
    assert "AUTO_TEST_GEN_TARGET_DIRS" in result["reason"]

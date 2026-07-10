"""Tests de l'auto-refactor DevAgent (code dupliqué → extraction, tests-gated)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DUP_BLOCK = """def compute(x, y):
    total = x + y
    total *= 2
    total -= 1
    result = total / 3
    return result
"""


def _make_project(tmp_path: Path) -> Path:
    from agents.devagent.executor import git_init

    project = tmp_path / "proj"
    src = project / "src"
    src.mkdir(parents=True)
    (src / "a.py").write_text(DUP_BLOCK, encoding="utf-8")
    (src / "b.py").write_text(DUP_BLOCK, encoding="utf-8")
    git_init(project)
    return project


@pytest.mark.asyncio
async def test_no_duplicate_is_a_noop(tmp_path):
    from agents.devagent.refactor import refactor_top_duplicate

    project = tmp_path / "empty"
    (project / "src").mkdir(parents=True)
    from agents.devagent.executor import git_init
    git_init(project)

    result = await refactor_top_duplicate(project)
    assert result == {"ok": True, "applied": False, "reason": "aucune duplication détectée"}


@pytest.mark.asyncio
async def test_refactor_applies_and_commits_when_tests_pass(tmp_path):
    from agents.devagent.executor import run_isolated
    from agents.devagent.refactor import refactor_top_duplicate

    project = _make_project(tmp_path)
    fake_payload = {
        "files": {
            "shared.py": "def compute(x, y):\n    return ((x + y) * 2 - 1) / 3\n",
            "a.py": "from shared import compute\n",
            "b.py": "from shared import compute\n",
        },
        "summary": "extrait compute() dans shared.py",
    }
    fake_response = {"content": json.dumps(fake_payload), "tokens_total": 42}

    with patch("agents.devagent.refactor.call_deepseek", new=AsyncMock(return_value=fake_response)):
        result = await refactor_top_duplicate(project, test_command="true")

    assert result["ok"] is True and result["applied"] is True
    assert (project / "src" / "shared.py").exists()
    log = run_isolated(["git", "log", "--oneline"], cwd=project)["stdout"]
    assert "refactor" in log.lower()


@pytest.mark.asyncio
async def test_refactor_reverts_when_tests_fail(tmp_path):
    from agents.devagent.refactor import refactor_top_duplicate

    project = _make_project(tmp_path)
    original_a = (project / "src" / "a.py").read_text(encoding="utf-8")
    fake_payload = {
        "files": {"a.py": "ceci n'est pas du python valide !!!", "b.py": "from a import x\n"},
        "summary": "refactor cassé",
    }
    fake_response = {"content": json.dumps(fake_payload), "tokens_total": 10}

    with patch("agents.devagent.refactor.call_deepseek", new=AsyncMock(return_value=fake_response)):
        result = await refactor_top_duplicate(project, test_command="false")

    assert result["ok"] is False and result["applied"] is False
    assert "tests rouges" in result["reason"]
    # le contenu original est restauré tel quel
    assert (project / "src" / "a.py").read_text(encoding="utf-8") == original_a


@pytest.mark.asyncio
async def test_refactor_new_file_removed_on_revert(tmp_path):
    """Un fichier CRÉÉ par le refactor (n'existait pas avant) est supprimé au rollback."""
    from agents.devagent.refactor import refactor_top_duplicate

    project = _make_project(tmp_path)
    fake_payload = {
        "files": {"new_shared.py": "def x(): pass\n"},
        "summary": "nouveau fichier",
    }
    fake_response = {"content": json.dumps(fake_payload), "tokens_total": 5}

    with patch("agents.devagent.refactor.call_deepseek", new=AsyncMock(return_value=fake_response)):
        result = await refactor_top_duplicate(project, test_command="false")

    assert result["applied"] is False
    assert not (project / "src" / "new_shared.py").exists()


@pytest.mark.asyncio
async def test_no_src_dir_is_reported(tmp_path):
    from agents.devagent.refactor import refactor_top_duplicate

    project = tmp_path / "nosrc"
    project.mkdir()
    result = await refactor_top_duplicate(project)
    assert result == {"ok": False, "applied": False, "reason": "pas de dossier src/"}

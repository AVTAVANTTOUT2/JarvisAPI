"""Tests de la génération auto de description de PR + changelog (DevAgent)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _make_repo(tmp_path: Path) -> Path:
    from agents.devagent.executor import git_commit, git_init

    project = tmp_path / "proj"
    project.mkdir()
    git_init(project)
    (project / "src").mkdir()
    (project / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
    git_commit(project, "feat: add a.py")
    (project / "src" / "b.py").write_text("print('b')\n", encoding="utf-8")
    git_commit(project, "feat: add b.py")
    return project


@pytest.mark.asyncio
async def test_generate_pr_description_writes_markdown(tmp_path):
    from agents.devagent.pr import generate_pr_description

    project = _make_repo(tmp_path)
    fake_payload = {
        "title": "Ajout des modules a et b",
        "summary": "Cette PR ajoute deux modules Python de base.",
        "changelog": ["Ajout de a.py", "Ajout de b.py"],
        "test_plan": ["Lancer python src/a.py", "Lancer python src/b.py"],
    }
    fake_response = {"content": json.dumps(fake_payload), "tokens_total": 30}

    with patch("agents.devagent.pr.call_deepseek", new=AsyncMock(return_value=fake_response)):
        result = await generate_pr_description(project, "Mon Projet")

    assert result["ok"] is True
    assert result["title"] == "Ajout des modules a et b"
    assert len(result["changelog"]) == 2
    md_path = Path(result["path"])
    assert md_path.exists()
    content = md_path.read_text(encoding="utf-8")
    assert "Ajout des modules a et b" in content
    assert "Ajout de a.py" in content
    assert "- [ ] Lancer python src/a.py" in content


@pytest.mark.asyncio
async def test_generate_pr_description_fallback_without_llm(tmp_path):
    from agents.devagent.pr import generate_pr_description

    project = _make_repo(tmp_path)
    with patch("agents.devagent.pr.call_deepseek", new=AsyncMock(side_effect=RuntimeError("down"))):
        result = await generate_pr_description(project, "Mon Projet")

    assert result["ok"] is True  # ne casse jamais, fallback brut sur le git log
    assert "feat: add" in "\n".join(result["changelog"])


@pytest.mark.asyncio
async def test_generate_pr_description_empty_history_reported(tmp_path):
    from agents.devagent.executor import git_init
    from agents.devagent.pr import generate_pr_description

    project = tmp_path / "empty_proj"
    project.mkdir()
    git_init(project)  # git_init fait déjà un commit "init"

    # base explicite = HEAD actuel → aucun commit entre base et HEAD
    from agents.devagent.executor import git_current_sha
    sha = git_current_sha(project)
    result = await generate_pr_description(project, "Vide", base=sha)
    assert result["ok"] is False
    assert "aucun commit" in result["reason"]


def test_open_pull_request_without_gh_cli(tmp_path, monkeypatch):
    from agents.devagent.pr import open_pull_request

    monkeypatch.setattr("agents.devagent.pr._gh_available", lambda: False)
    result = open_pull_request(tmp_path, "Titre", "Corps")
    assert result["opened"] is False
    assert "gh" in result["reason"]


def test_open_pull_request_without_remote(tmp_path, monkeypatch):
    from agents.devagent.executor import git_init
    from agents.devagent.pr import open_pull_request

    git_init(tmp_path)
    monkeypatch.setattr("agents.devagent.pr._gh_available", lambda: True)
    result = open_pull_request(tmp_path, "Titre", "Corps")
    assert result["opened"] is False
    assert "remote" in result["reason"]

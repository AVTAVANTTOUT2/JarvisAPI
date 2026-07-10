"""Test d'intégration : boucle DevAgent complète avec perf-guard, staging, PR auto.

Vérifie que le câblage plan → code → test → commit → perf-guard →
staging → jugement d'acceptation → PR auto fonctionne de bout en bout sur
une itération réelle (git réel, venv réel), avec seulement les appels
DeepSeek mockés.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


async def _fake_call_deepseek(system: str, user: str, **kwargs):
    if user == "Planifie la prochaine tache.":
        payload = {"task": "ajoute un test", "files_to_create_or_edit": ["test_ok.py"], "reasoning": "x"}
    elif user == "Genere le code.":
        payload = {
            "files": {"test_ok.py": "def test_ok():\n    assert True\n"},
            "test_command": "python3 -m pytest -q src/test_ok.py",
        }
    elif user == "Evalue les criteres d'acceptation.":
        payload = {"done": True, "reason": "critères satisfaits"}
    elif user == "Génère la description de PR.":
        payload = {
            "title": "Ajout du test smoke",
            "summary": "Ajoute un test de validation basique.",
            "changelog": ["Ajout de test_ok.py"],
            "test_plan": ["Lancer la suite de tests"],
        }
    else:
        raise AssertionError(f"appel DeepSeek inattendu : user={user!r}")
    return {"content": json.dumps(payload), "tokens_total": 20}


@pytest.mark.asyncio
async def test_full_loop_iteration_with_perf_staging_pr(tmp_db, tmp_path, monkeypatch):
    from database import devagent as devagent_db
    from database import get_perf_history, get_unread_notifications
    from database.devagent import get_deployments

    project_path = tmp_path / "myproj"
    project_path.mkdir()
    project_id = devagent_db.create_dev_project("myproj", "Mon Projet", str(project_path))
    spec = {
        "project_name": "Mon Projet", "slug": "myproj", "project_type": "cli",
        "stack": ["python"], "isolation_path": str(project_path),
        "constraints": [], "acceptance_criteria": ["les tests passent"],
        "loop_budget": {"max_iterations": 3, "max_tokens": 1_000_000, "max_consecutive_failures": 3},
    }
    devagent_db.save_spec(project_id, json.dumps(spec))

    monkeypatch.setattr("config.DEVAGENT_AUTO_DEPLOY_STAGING", True)
    monkeypatch.setattr("config.DEVAGENT_AUTO_PR", True)

    with patch("agents.devagent.loop.call_deepseek", new=AsyncMock(side_effect=_fake_call_deepseek)), \
         patch("agents.devagent.pr.call_deepseek", new=AsyncMock(side_effect=_fake_call_deepseek)):
        from agents.devagent.loop import run_loop

        await run_loop(project_id)

    project = devagent_db.get_project(project_id)
    assert project["status"] == "done"

    # Un commit d'itération a bien été créé (au-delà du commit "init").
    from agents.devagent.executor import run_isolated
    log = run_isolated(["git", "log", "--oneline"], cwd=project_path)["stdout"]
    assert "iteration 0" in log

    # Perf-guard a enregistré un benchmark pour le scope du projet.
    assert len(get_perf_history("myproj")) == 1

    # Déploiement staging exécuté et enregistré en succès.
    deployments = get_deployments(project_id)
    assert len(deployments) == 1
    assert deployments[0]["status"] == "success"
    assert Path(deployments[0]["staging_path"]).is_dir()

    # PR auto-générée à l'acceptation.
    pr_path = project_path / "PR_DESCRIPTION.md"
    assert pr_path.exists()
    assert "Ajout du test smoke" in pr_path.read_text(encoding="utf-8")

    logs = devagent_db.get_dev_loop_logs(limit=50, project_id=project_id)
    phases = {log["action_type"] for log in logs}
    assert "devagent_perf_guard" in phases
    assert "devagent_staging" in phases
    assert "devagent_pr" in phases


@pytest.mark.asyncio
async def test_loop_disables_staging_and_pr_via_config(tmp_db, tmp_path, monkeypatch):
    from database import devagent as devagent_db
    from database.devagent import get_deployments

    project_path = tmp_path / "myproj2"
    project_path.mkdir()
    project_id = devagent_db.create_dev_project("myproj2", "Projet 2", str(project_path))
    spec = {
        "project_name": "Projet 2", "slug": "myproj2", "project_type": "cli",
        "stack": ["python"], "isolation_path": str(project_path),
        "constraints": [], "acceptance_criteria": ["les tests passent"],
        "loop_budget": {"max_iterations": 3, "max_tokens": 1_000_000, "max_consecutive_failures": 3},
    }
    devagent_db.save_spec(project_id, json.dumps(spec))

    monkeypatch.setattr("config.DEVAGENT_AUTO_DEPLOY_STAGING", False)
    monkeypatch.setattr("config.DEVAGENT_AUTO_PR", False)

    with patch("agents.devagent.loop.call_deepseek", new=AsyncMock(side_effect=_fake_call_deepseek)):
        from agents.devagent.loop import run_loop

        await run_loop(project_id)

    assert devagent_db.get_project(project_id)["status"] == "done"
    assert get_deployments(project_id) == []
    assert not (project_path / "PR_DESCRIPTION.md").exists()

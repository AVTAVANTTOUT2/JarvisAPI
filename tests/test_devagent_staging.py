"""Tests du déploiement staging DevAgent (archive git, tests-gated, jamais bloquant)."""

from __future__ import annotations

import sys
from pathlib import Path

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


def _make_project(tmp_path: Path, passing: bool) -> Path:
    from agents.devagent.executor import git_commit, git_init

    project = tmp_path / "proj"
    project.mkdir()
    (project / "test_smoke.py").write_text(
        "def test_smoke():\n    assert " + ("True" if passing else "False") + "\n",
        encoding="utf-8",
    )
    git_init(project)
    git_commit(project, "add smoke test")
    return project


def test_deploy_success_when_tests_pass(tmp_db, tmp_path):
    from agents.devagent.staging import deploy_to_staging
    from database.devagent import create_dev_project, get_deployments

    project = _make_project(tmp_path, passing=True)
    project_id = create_dev_project("proj", "Proj", str(project))

    result = deploy_to_staging(project_id, project, test_command="python3 -m pytest -q test_smoke.py")
    assert result["ok"] is True
    staging = Path(result["staging_path"])
    assert staging.is_dir()
    assert (staging / "test_smoke.py").exists()

    deployments = get_deployments(project_id)
    assert len(deployments) == 1
    assert deployments[0]["status"] == "success"


def test_deploy_failed_when_tests_fail(tmp_db, tmp_path):
    from agents.devagent.staging import deploy_to_staging
    from database.devagent import create_dev_project, get_deployments

    project = _make_project(tmp_path, passing=False)
    project_id = create_dev_project("proj2", "Proj2", str(project))

    result = deploy_to_staging(project_id, project, test_command="python3 -m pytest -q test_smoke.py")
    assert result["ok"] is False
    deployments = get_deployments(project_id)
    assert deployments[0]["status"] == "failed"


def test_deploy_only_ships_committed_content(tmp_db, tmp_path):
    """Un fichier non commité ne doit PAS apparaître dans le staging (git archive HEAD)."""
    from agents.devagent.staging import deploy_to_staging
    from database.devagent import create_dev_project

    project = _make_project(tmp_path, passing=True)
    (project / "uncommitted.txt").write_text("pas committé", encoding="utf-8")
    project_id = create_dev_project("proj3", "Proj3", str(project))

    result = deploy_to_staging(project_id, project, test_command="python3 -m pytest -q test_smoke.py")
    assert result["ok"] is True
    staging = Path(result["staging_path"])
    assert not (staging / "uncommitted.txt").exists()
    assert (staging / "test_smoke.py").exists()


def test_deploy_notifies(tmp_db, tmp_path):
    from agents.devagent.staging import deploy_to_staging
    from database import get_unread_notifications
    from database.devagent import create_dev_project

    project = _make_project(tmp_path, passing=True)
    project_id = create_dev_project("proj4", "Proj4", str(project))
    deploy_to_staging(project_id, project, test_command="python3 -m pytest -q test_smoke.py")

    notifs = [n for n in get_unread_notifications(10) if "staging" in n["title"].lower()]
    assert len(notifs) == 1

"""Tests de l'autorun DevAgent (interview auto-répondue → spec → boucle, zéro humain)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("agents.devagent.spec_builder.DEV_PROJECTS_ROOT", tmp_path / "dev_projects")
    monkeypatch.setattr("config.DEV_PROJECTS_ROOT", str(tmp_path / "dev_projects"))
    from database import init_db

    init_db()
    return tmp_path


async def _fake_run_loop(project_id):
    """Remplace la vraie boucle (testée ailleurs) — on ne vérifie ici que l'enchaînement autorun."""
    return None


@pytest.mark.asyncio
async def test_autorun_converges_after_two_questions(tmp_env):
    """L'interview auto-répondue converge, la spec est verrouillée, la boucle est lancée."""
    from agents.devagent import autorun

    call_log = []

    async def fake_interview_call(system, user, **kwargs):
        if user == "Continue l'interview.":
            n = sum(1 for c in call_log if c == "interview")
            call_log.append("interview")
            if n < 2:
                payload = {"done": False, "question": f"Question {n}?", "type": "text"}
            else:
                payload = {"done": True, "spec": {
                    "project_name": "CLI météo", "project_type": "cli", "stack": ["python"],
                    "constraints": [], "acceptance_criteria": ["les tests passent"],
                    "loop_budget": {"max_iterations": 5, "max_tokens": 100000, "max_consecutive_failures": 3},
                }}
            return {"content": json.dumps(payload), "tokens_total": 10}
        if user == "Réponds à la question.":
            return {"content": json.dumps({"answer": "réponse auto cohérente"}), "tokens_total": 5}
        raise AssertionError(f"appel inattendu : {user}")

    with patch("agents.devagent.interview.call_deepseek", new=AsyncMock(side_effect=fake_interview_call)), \
         patch("agents.devagent.autorun.call_deepseek", new=AsyncMock(side_effect=fake_interview_call)), \
         patch("agents.devagent.autorun.run_loop", new=AsyncMock(side_effect=_fake_run_loop)) as mock_loop:
        result = await autorun.autorun_project("Fais-moi un CLI météo en Python", name="CLI météo")

    assert result["slug"] == "cli-meteo"
    assert result["interview_rounds"] == 2
    assert result["spec"]["project_name"] == "CLI météo"

    from database import devagent as devagent_db
    project = devagent_db.get_project(result["project_id"])
    assert project["status"] == "running"
    assert project["spec_json"] is not None
    await asyncio.sleep(0)  # laisse la task create_task() s'exécuter
    mock_loop.assert_awaited_once_with(result["project_id"])


@pytest.mark.asyncio
async def test_autorun_forces_finalization_at_round_limit(tmp_env, monkeypatch):
    """Si l'interview ne converge jamais, on force la finalisation après le plafond configuré."""
    from agents.devagent import autorun

    monkeypatch.setattr("config.DEVAGENT_AUTORUN_MAX_INTERVIEW_ROUNDS", 2)

    async def always_asking(system, user, **kwargs):
        if user == "Continue l'interview.":
            return {"content": json.dumps({"done": False, "question": "Encore ?", "type": "text"}),
                    "tokens_total": 5}
        return {"content": json.dumps({"answer": "ok"}), "tokens_total": 5}

    # Après le plafond, le prochain appel "Continue l'interview." doit produire une spec.
    call_count = {"n": 0}

    async def eventually_converges(system, user, **kwargs):
        if user == "Continue l'interview.":
            call_count["n"] += 1
            if call_count["n"] <= 3:
                return {"content": json.dumps({"done": False, "question": "Encore ?", "type": "text"}),
                        "tokens_total": 5}
            return {"content": json.dumps({"done": True, "spec": {
                "project_name": "Projet forcé", "project_type": "cli", "stack": ["python"],
                "constraints": [], "acceptance_criteria": [],
                "loop_budget": {"max_iterations": 1, "max_tokens": 1000, "max_consecutive_failures": 1},
            }}), "tokens_total": 5}
        return {"content": json.dumps({"answer": "ok"}), "tokens_total": 5}

    with patch("agents.devagent.interview.call_deepseek", new=AsyncMock(side_effect=eventually_converges)), \
         patch("agents.devagent.autorun.call_deepseek", new=AsyncMock(side_effect=eventually_converges)), \
         patch("agents.devagent.autorun.run_loop", new=AsyncMock(side_effect=_fake_run_loop)):
        result = await autorun.autorun_project("Demande vague", name="Projet forcé")

    assert result["spec"]["project_name"] == "Projet forcé"
    # 2 tours normaux + 1 tour de finalisation forcée
    assert result["interview_rounds"] == 3


@pytest.mark.asyncio
async def test_autorun_raises_when_interview_never_converges(tmp_env, monkeypatch):
    from agents.devagent import autorun

    monkeypatch.setattr("config.DEVAGENT_AUTORUN_MAX_INTERVIEW_ROUNDS", 1)

    async def never_converges(system, user, **kwargs):
        if user == "Continue l'interview.":
            return {"content": json.dumps({"done": False, "question": "Encore ?", "type": "text"}),
                    "tokens_total": 5}
        return {"content": json.dumps({"answer": "ok"}), "tokens_total": 5}

    with patch("agents.devagent.interview.call_deepseek", new=AsyncMock(side_effect=never_converges)), \
         patch("agents.devagent.autorun.call_deepseek", new=AsyncMock(side_effect=never_converges)):
        with pytest.raises(RuntimeError, match="n'a pas convergé"):
            await autorun.autorun_project("Demande impossible")

    from database import devagent as devagent_db
    projects_status = [p for p in [devagent_db.get_project(1)] if p]
    assert projects_status[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_autorun_slug_collision_gets_suffixed(tmp_env):
    from agents.devagent import autorun

    async def fake_immediate_done(system, user, **kwargs):
        if user == "Continue l'interview.":
            return {"content": json.dumps({"done": True, "spec": {
                "project_name": "Todo", "project_type": "cli", "stack": ["python"],
                "constraints": [], "acceptance_criteria": [],
                "loop_budget": {"max_iterations": 1, "max_tokens": 1000, "max_consecutive_failures": 1},
            }}), "tokens_total": 5}
        return {"content": json.dumps({"answer": "ok"}), "tokens_total": 5}

    with patch("agents.devagent.interview.call_deepseek", new=AsyncMock(side_effect=fake_immediate_done)), \
         patch("agents.devagent.autorun.call_deepseek", new=AsyncMock(side_effect=fake_immediate_done)), \
         patch("agents.devagent.autorun.run_loop", new=AsyncMock(side_effect=_fake_run_loop)):
        first = await autorun.autorun_project("Une todo app", name="Todo")
        second = await autorun.autorun_project("Une todo app encore", name="Todo")

    assert first["slug"] == "todo"
    assert second["slug"] != "todo"
    assert second["slug"].startswith("todo-")

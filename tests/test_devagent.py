"""Tests unitaires DevAgent — interview, spec_builder, executor."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_projects_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr("agents.devagent.spec_builder.DEV_PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr("config.DEV_PROJECTS_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    yield db_path


def test_slugify_normalizes_french_text() -> None:
    from agents.devagent.utils import slugify

    assert slugify("Mon API Météo") == "mon-api-meteo"
    assert slugify("  Hello World!!! ") == "hello-world"


def test_parse_json_response_strips_markdown_fence() -> None:
    from agents.devagent.utils import parse_json_response

    raw = '```json\n{"done": false, "question": "Type?"}\n```'
    data = parse_json_response(raw)
    assert data["done"] is False
    assert data["question"] == "Type?"


def test_lock_spec_writes_spec_and_state(tmp_projects_root: Path) -> None:
    from agents.devagent.spec_builder import lock_spec

    spec = lock_spec(
        {
            "project_name": "Todo CLI",
            "project_type": "cli",
            "stack": ["python"],
            "constraints": ["stdlib only"],
            "acceptance_criteria": ["pytest passes"],
        }
    )
    assert spec.slug == "todo-cli"
    spec_path = tmp_projects_root / "todo-cli" / "spec.json"
    state_path = tmp_projects_root / "todo-cli" / ".devagent_state.json"
    assert spec_path.exists()
    assert state_path.exists()
    loaded = json.loads(spec_path.read_text(encoding="utf-8"))
    assert loaded["project_name"] == "Todo CLI"
    assert (tmp_projects_root / "todo-cli" / "src").is_dir()


def test_build_isolation_path_creates_directories(tmp_projects_root: Path) -> None:
    from agents.devagent.spec_builder import build_isolation_path

    path = build_isolation_path("my-app")
    assert path.exists()
    assert (path / "src").is_dir()


def test_run_isolated_executes_in_cwd(tmp_path: Path) -> None:
    from agents.devagent.executor import run_isolated

    script = tmp_path / "hello.txt"
    result = run_isolated(["touch", "hello.txt"], cwd=tmp_path, timeout=10)
    assert result["returncode"] == 0
    assert script.exists()


def test_run_isolated_timeout_raises(tmp_path: Path) -> None:
    from agents.devagent.executor import ExecutionTimeout, run_isolated

    with pytest.raises(ExecutionTimeout):
        run_isolated(["sleep", "5"], cwd=tmp_path, timeout=1)


def test_git_commit_after_file_change(tmp_path: Path) -> None:
    from agents.devagent.executor import git_commit, git_init, run_isolated

    git_init(tmp_path)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    result = git_commit(tmp_path, "add main")
    assert result["returncode"] == 0
    log = run_isolated(["git", "log", "--oneline"], cwd=tmp_path)
    assert "add main" in log["stdout"]


@pytest.mark.asyncio
async def test_submit_answer_appends_history() -> None:
    from agents.devagent.interview import submit_answer

    fake_response = {
        "content": json.dumps(
            {
                "done": False,
                "question": "Stack?",
                "type": "qcm_or_text",
                "options": ["Python", "Node"],
            }
        ),
        "tokens_total": 10,
    }
    context: dict = {"qa_history": []}

    with patch(
        "agents.devagent.interview.call_deepseek",
        new=AsyncMock(return_value=fake_response),
    ):
        result = await submit_answer(1, "Type?", "API REST", context)

    assert len(context["qa_history"]) == 1
    assert context["qa_history"][0]["a"] == "API REST"
    assert result["question"] == "Stack?"


@pytest.mark.asyncio
async def test_next_interview_step_returns_spec_when_done() -> None:
    from agents.devagent.interview import next_interview_step

    spec_payload = {
        "done": True,
        "spec": {
            "project_name": "Ping API",
            "project_type": "api",
            "stack": ["fastapi"],
            "constraints": [],
            "acceptance_criteria": ["health endpoint"],
            "loop_budget": {
                "max_iterations": 5,
                "max_tokens": 1000,
                "max_consecutive_failures": 2,
            },
        },
    }
    fake_response = {
        "content": json.dumps(spec_payload),
        "tokens_total": 20,
    }

    with patch(
        "agents.devagent.interview.call_deepseek",
        new=AsyncMock(return_value=fake_response),
    ):
        result = await next_interview_step(1, {"qa_history": [{"q": "x", "a": "y"}]})

    assert result["done"] is True
    assert result["spec"]["project_name"] == "Ping API"


def test_devagent_db_create_and_status(tmp_db: Path, tmp_projects_root: Path) -> None:
    from database import devagent as devagent_db

    project_id = devagent_db.create_dev_project(
        "demo", "Demo App", str(tmp_projects_root / "demo")
    )
    project = devagent_db.get_project(project_id)
    assert project is not None
    assert project["status"] == "interviewing"

    devagent_db.save_interview_context(project_id, {"qa_history": [{"q": "a", "a": "b"}]})
    ctx = devagent_db.get_interview_context(project_id)
    assert len(ctx["qa_history"]) == 1

    spec_json = json.dumps({"project_name": "Demo App", "slug": "demo"})
    devagent_db.save_spec(project_id, spec_json)
    devagent_db.update_project_status(project_id, "spec_locked")

    payload = devagent_db.get_project_status_payload(project_id)
    assert payload["status"] == "spec_locked"
    assert payload["spec_locked"] is True


def test_dev_loop_logs_exposed_for_api(tmp_db: Path) -> None:
    from database import devagent as devagent_db

    project_id = devagent_db.create_dev_project("x", "X", "/tmp/x")
    devagent_db.log_iteration(project_id, 0, "plan", '{"task":"init"}', True)
    logs = devagent_db.get_dev_loop_logs(limit=10)
    assert len(logs) == 1
    assert logs[0]["agent"] == "devagent"
    assert logs[0]["action_type"] == "devagent_plan"


def test_get_llm_logs_merges_devagent(tmp_db: Path) -> None:
    from database import devagent as devagent_db, get_llm_logs

    project_id = devagent_db.create_dev_project("y", "Y", "/tmp/y")
    devagent_db.log_iteration(project_id, 1, "test", "ok", True)

    merged = get_llm_logs(limit=50)
    assert any(row.get("agent") == "devagent" for row in merged)

    filtered = get_llm_logs(limit=50, action_type="devagent")
    assert all(row.get("agent") == "devagent" for row in filtered)

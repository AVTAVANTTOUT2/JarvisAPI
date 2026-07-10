"""Tests : calculateur de coût de la procrastination (heuristique sur tasks)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.PROCRASTINATION_ABANDONED_DAYS", 30)
    monkeypatch.setattr("config.PROCRASTINATION_HOURLY_VALUE", 0.0)
    from database import init_db

    init_db()
    return db_path


def test_flags_task_older_than_threshold():
    from scripts.procrastination_cost import compute_procrastination_cost

    now = datetime(2026, 7, 10)
    old_created = (now - timedelta(days=40)).isoformat()
    tasks = [{"id": 1, "title": "Ranger le garage", "status": "todo", "created_at": old_created}]

    result = compute_procrastination_cost(tasks, now=now, abandoned_days=30)
    assert len(result["abandoned_tasks"]) == 1
    assert result["abandoned_tasks"][0]["days_pending"] == 40


def test_ignores_task_under_threshold():
    from scripts.procrastination_cost import compute_procrastination_cost

    now = datetime(2026, 7, 10)
    recent = (now - timedelta(days=5)).isoformat()
    tasks = [{"id": 1, "title": "Répondre au mail", "status": "todo", "created_at": recent}]

    result = compute_procrastination_cost(tasks, now=now, abandoned_days=30)
    assert result["abandoned_tasks"] == []
    assert result["total_days_pending"] == 0


def test_ignores_done_tasks_regardless_of_age():
    from scripts.procrastination_cost import compute_procrastination_cost

    now = datetime(2026, 7, 10)
    old = (now - timedelta(days=90)).isoformat()
    tasks = [{"id": 1, "title": "Vieille tâche faite", "status": "done", "created_at": old}]

    result = compute_procrastination_cost(tasks, now=now, abandoned_days=30)
    assert result["abandoned_tasks"] == []


def test_estimated_cost_is_none_when_hourly_value_zero():
    from scripts.procrastination_cost import compute_procrastination_cost

    now = datetime(2026, 7, 10)
    old = (now - timedelta(days=40)).isoformat()
    tasks = [{"id": 1, "title": "T", "status": "todo", "created_at": old}]

    result = compute_procrastination_cost(tasks, now=now, abandoned_days=30, hourly_value=0)
    assert result["estimated_cost"] is None


def test_estimated_cost_computed_when_hourly_value_set():
    from scripts.procrastination_cost import compute_procrastination_cost

    now = datetime(2026, 7, 10)
    old = (now - timedelta(days=60)).isoformat()  # 60 jours * 10 min/jour = 10h
    tasks = [{"id": 1, "title": "T", "status": "todo", "created_at": old}]

    result = compute_procrastination_cost(tasks, now=now, abandoned_days=30, hourly_value=20.0)
    assert result["overhead_hours"] == 10.0
    assert result["estimated_cost"] == 200.0


def test_multiple_tasks_sorted_by_days_pending_desc():
    from scripts.procrastination_cost import compute_procrastination_cost

    now = datetime(2026, 7, 10)
    tasks = [
        {"id": 1, "title": "A", "status": "todo", "created_at": (now - timedelta(days=35)).isoformat()},
        {"id": 2, "title": "B", "status": "doing", "created_at": (now - timedelta(days=90)).isoformat()},
    ]
    result = compute_procrastination_cost(tasks, now=now, abandoned_days=30)
    assert [t["id"] for t in result["abandoned_tasks"]] == [2, 1]
    assert result["total_days_pending"] == 125


def test_get_procrastination_cost_uses_real_db(tmp_db):
    from database import create_task, get_db
    from scripts.procrastination_cost import get_procrastination_cost

    task_id = create_task("Tâche ancienne", category="perso")
    old_date = (datetime.now() - timedelta(days=45)).isoformat()
    with get_db() as conn:
        conn.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (old_date, task_id))

    result = get_procrastination_cost()
    assert len(result["abandoned_tasks"]) == 1
    assert result["abandoned_tasks"][0]["title"] == "Tâche ancienne"

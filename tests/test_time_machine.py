"""Tests : machine à remonter le temps (reconstruction chronologique d'une journée)."""

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


def test_empty_day_returns_zeroed_summary(tmp_db):
    from scripts.time_machine import build_day_timeline

    result = build_day_timeline("2020-01-01")
    assert result["timeline"] == []
    assert result["summary"]["messages"] == 0
    assert result["journal_entry"] is None


def test_task_done_appears_in_timeline(tmp_db):
    from database import create_task, get_db, update_task_status
    from scripts.time_machine import build_day_timeline

    task_id = create_task("Rendre le rapport")
    update_task_status(task_id, "done")
    with get_db() as conn:
        conn.execute(
            "UPDATE tasks SET completed_at = '2026-07-10 14:30:00' WHERE id = ?", (task_id,)
        )

    result = build_day_timeline("2026-07-10")
    task_events = [e for e in result["timeline"] if e["type"] == "task_done"]
    assert len(task_events) == 1
    assert task_events[0]["time"] == "14:30"
    assert "Rendre le rapport" in task_events[0]["description"]


def test_visit_appears_in_timeline(tmp_db):
    from database import get_db
    from scripts.time_machine import build_day_timeline

    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO places (name, category, latitude, longitude) VALUES ('Bureau', 'work', 1, 1)"
        )
        place_id = cur.lastrowid
        conn.execute(
            "INSERT INTO visits (place_id, arrived_at, departed_at, duration_min) "
            "VALUES (?, '2026-07-10 09:00:00', '2026-07-10 17:00:00', 480)",
            (place_id,),
        )

    result = build_day_timeline("2026-07-10")
    visit_events = [e for e in result["timeline"] if e["type"] == "visit"]
    assert len(visit_events) == 1
    assert "Bureau" in visit_events[0]["description"]
    assert result["summary"]["visits"] == 1


def test_timeline_sorted_chronologically(tmp_db):
    from database import get_db
    from scripts.time_machine import build_day_timeline

    with get_db() as conn:
        conn.execute(
            "INSERT INTO mood_log (mood_score, energy_level, created_at) VALUES (7, 6, '2026-07-10 20:00:00')"
        )
        conn.execute(
            "INSERT INTO screen_activity (app, notable, mood, created_at) "
            "VALUES ('VSCode', 'a débloqué un bug', 'focused', '2026-07-10 08:00:00')"
        )

    result = build_day_timeline("2026-07-10")
    times = [e["time"] for e in result["timeline"]]
    assert times == sorted(times)
    assert times[0] == "08:00"
    assert times[-1] == "20:00"


def test_journal_entry_included_if_present(tmp_db):
    from database import upsert_jarvis_journal_entry
    from scripts.time_machine import build_day_timeline

    upsert_jarvis_journal_entry("2026-07-10", "Journée productive, Monsieur.")
    result = build_day_timeline("2026-07-10")
    assert result["journal_entry"] == "Journée productive, Monsieur."


def test_endpoint_returns_timeline(tmp_db):
    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        r = client.get("/api/time-machine/2026-07-10")
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == "2026-07-10"
    assert "timeline" in body

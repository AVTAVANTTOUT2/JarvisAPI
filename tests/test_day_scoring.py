"""Tests : score jour exceptionnel + indice de chance (heuristique déterministe)."""

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


def test_neutral_day_scores_near_baseline():
    from scripts.day_scoring import compute_day_scores

    result = compute_day_scores({"tasks_done": 0, "tasks_overdue": 0})
    assert result["exceptional_score"] == 50
    assert result["luck_score"] == 50


def test_productive_day_scores_higher_exceptional():
    from scripts.day_scoring import compute_day_scores

    result = compute_day_scores({
        "tasks_done": 4, "tasks_overdue": 0, "mood_score": 9, "energy_level": 8,
        "screen_mood_counts": {"focused": 8, "distracted": 1},
    })
    assert result["exceptional_score"] > 80


def test_bad_day_scores_lower():
    from scripts.day_scoring import compute_day_scores

    result = compute_day_scores({
        "tasks_done": 0, "tasks_overdue": 3, "mood_score": 2, "energy_level": 2,
        "screen_mood_counts": {"distracted": 5, "stuck": 3},
    })
    assert result["exceptional_score"] < 30
    assert result["luck_score"] < 40


def test_scores_are_clamped_to_0_100():
    from scripts.day_scoring import compute_day_scores

    result = compute_day_scores({
        "tasks_done": 100, "tasks_overdue": 0, "mood_score": 10, "energy_level": 10,
        "screen_mood_counts": {"focused": 100},
    })
    assert 0 <= result["exceptional_score"] <= 100
    assert 0 <= result["luck_score"] <= 100

    result2 = compute_day_scores({
        "tasks_done": 0, "tasks_overdue": 50, "mood_score": 1, "energy_level": 1,
        "screen_mood_counts": {"distracted": 100},
    })
    assert 0 <= result2["exceptional_score"] <= 100
    assert 0 <= result2["luck_score"] <= 100


def test_factors_breakdown_present_and_matches_score():
    from scripts.day_scoring import compute_day_scores

    result = compute_day_scores({"tasks_done": 2, "tasks_overdue": 1, "mood_score": 7})
    assert "exceptional" in result["factors"]
    assert "luck" in result["factors"]
    assert "tasks_done_bonus" in result["factors"]["exceptional"]


def test_smooth_day_bonus_applies_to_luck_only_when_tasks_done_and_no_overdue():
    from scripts.day_scoring import compute_day_scores

    smooth = compute_day_scores({"tasks_done": 1, "tasks_overdue": 0})
    rough = compute_day_scores({"tasks_done": 0, "tasks_overdue": 0})
    assert smooth["luck_score"] > rough["luck_score"]


def test_score_day_persists_to_db(tmp_db):
    from database import create_task, get_db, update_task_status
    from scripts.day_scoring import _today, score_day

    task_id = create_task("Terminer le rapport")
    update_task_status(task_id, "done")
    with get_db() as conn:
        conn.execute("UPDATE tasks SET completed_at = CURRENT_TIMESTAMP WHERE id = ?", (task_id,))

    result = score_day()
    assert result["date"] == _today()

    from database import get_day_score

    stored = get_day_score(_today())
    assert stored["exceptional_score"] == result["exceptional_score"]


def test_score_day_without_persist_does_not_write(tmp_db):
    from database import get_day_score
    from scripts.day_scoring import _today, score_day

    score_day(persist=False)
    assert get_day_score(_today()) is None

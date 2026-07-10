"""Tests : journal parallèle de JARVIS + scores quotidiens (jour exceptionnel / chance)."""

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


# ── Journal de JARVIS ─────────────────────────────────────────

def test_upsert_and_get_journal_entry(tmp_db):
    from database import get_jarvis_journal_entry, upsert_jarvis_journal_entry

    upsert_jarvis_journal_entry("2026-07-10", "Monsieur a couru toute la journée.")
    entry = get_jarvis_journal_entry("2026-07-10")
    assert entry["entry"] == "Monsieur a couru toute la journée."


def test_upsert_journal_entry_overwrites_same_date(tmp_db):
    from database import get_jarvis_journal_entry, upsert_jarvis_journal_entry

    upsert_jarvis_journal_entry("2026-07-10", "Première version.")
    upsert_jarvis_journal_entry("2026-07-10", "Version corrigée.")
    entry = get_jarvis_journal_entry("2026-07-10")
    assert entry["entry"] == "Version corrigée."


def test_get_journal_entries_ordered_recent_first(tmp_db):
    from database import get_jarvis_journal_entries, upsert_jarvis_journal_entry

    upsert_jarvis_journal_entry("2026-07-08", "Jour 1")
    upsert_jarvis_journal_entry("2026-07-09", "Jour 2")
    upsert_jarvis_journal_entry("2026-07-10", "Jour 3")

    entries = get_jarvis_journal_entries(days=7)
    assert [e["date"] for e in entries] == ["2026-07-10", "2026-07-09", "2026-07-08"]


def test_get_journal_entry_missing_date_returns_none(tmp_db):
    from database import get_jarvis_journal_entry

    assert get_jarvis_journal_entry("2026-01-01") is None


# ── Scores quotidiens ─────────────────────────────────────────

def test_upsert_and_get_day_score(tmp_db):
    from database import get_day_score, upsert_day_score

    upsert_day_score("2026-07-10", exceptional_score=95, luck_score=40, factors={"raison": "test"})
    score = get_day_score("2026-07-10")
    assert score["exceptional_score"] == 95
    assert score["luck_score"] == 40
    assert score["factors_json"]


def test_upsert_day_score_overwrites(tmp_db):
    from database import get_day_score, upsert_day_score

    upsert_day_score("2026-07-10", exceptional_score=50, luck_score=50, factors={})
    upsert_day_score("2026-07-10", exceptional_score=90, luck_score=10, factors={})
    score = get_day_score("2026-07-10")
    assert score["exceptional_score"] == 90
    assert score["luck_score"] == 10


def test_get_top_days_by_exceptional_score(tmp_db):
    from database import get_top_days, upsert_day_score

    upsert_day_score("2026-07-08", exceptional_score=80, luck_score=60, factors={})
    upsert_day_score("2026-07-09", exceptional_score=30, luck_score=90, factors={})
    upsert_day_score("2026-07-10", exceptional_score=95, luck_score=40, factors={})

    top = get_top_days(metric="exceptional_score", limit=2, days=90)
    assert [d["date"] for d in top] == ["2026-07-10", "2026-07-08"]


def test_get_top_days_by_luck_score(tmp_db):
    from database import get_top_days, upsert_day_score

    upsert_day_score("2026-07-08", exceptional_score=80, luck_score=60, factors={})
    upsert_day_score("2026-07-09", exceptional_score=30, luck_score=90, factors={})
    upsert_day_score("2026-07-10", exceptional_score=95, luck_score=40, factors={})

    top = get_top_days(metric="luck_score", limit=1, days=90)
    assert top[0]["date"] == "2026-07-09"


def test_get_top_days_rejects_invalid_metric(tmp_db):
    from database import get_top_days

    with pytest.raises(ValueError):
        get_top_days(metric="drop table users;--", limit=5)

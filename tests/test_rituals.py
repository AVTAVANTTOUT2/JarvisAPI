"""Tests des rituels quotidiens — roast, debrief, score, anniversaires, pause, citation, easter eggs."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.RITUALS_TTS", False)  # pas de daemon en test
    from database import init_db

    init_db()
    return db_path


# ── Table daily_rituals ──────────────────────────────────────

def test_set_get_daily_ritual_upsert(tmp_db):
    from database import get_daily_ritual, set_daily_ritual

    set_daily_ritual("2026-07-09", "quote", "Première.")
    set_daily_ritual("2026-07-09", "quote", "Corrigée.")
    set_daily_ritual("2026-07-09", "roast", "Roast.")
    row = get_daily_ritual("2026-07-09")
    assert row["quote"] == "Corrigée."
    assert row["roast"] == "Roast."
    assert get_daily_ritual("2000-01-01") is None
    with pytest.raises(ValueError):
        set_daily_ritual("2026-07-09", "drop table", "x")


# ── Score productivité ───────────────────────────────────────

def test_productivity_score_deterministic(tmp_db):
    from database import get_db
    from scripts.rituals import compute_productivity_score

    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        for i in range(3):  # 3 tâches faites cette semaine
            conn.execute(
                "INSERT INTO tasks (title, status, completed_at) VALUES (?, 'done', ?)",
                (f"faite {i}", today),
            )
        conn.execute(  # 1 tâche en retard
            "INSERT INTO tasks (title, status, due_date) VALUES ('ratée', 'todo', '2020-01-01 10:00')")

    r = compute_productivity_score(persist=True)
    assert r["score"] == 50 + 8 * 3 - 12 * 1  # 62
    assert r["label"] == "Convenable."
    assert r["done_7d"] == 3 and r["overdue"] == 1

    from database import get_daily_ritual
    row = get_daily_ritual(datetime.now().strftime("%Y-%m-%d"))
    assert row["productivity_score"] == 62


def test_productivity_score_clamped(tmp_db):
    from database import get_db
    from scripts.rituals import compute_productivity_score

    with get_db() as conn:
        for i in range(10):
            conn.execute(
                "INSERT INTO tasks (title, status, due_date) VALUES (?, 'todo', '2020-01-01 10:00')",
                (f"retard {i}",))
    assert compute_productivity_score()["score"] == 0


# ── Anniversaires ────────────────────────────────────────────

def test_birthdays_matching_and_dedupe(tmp_db):
    from database import get_db, get_todays_birthdays
    from scripts.rituals import check_birthdays

    mm_dd = datetime.now().strftime("%m-%d")
    other = "01-02" if mm_dd != "01-02" else "01-03"
    with get_db() as conn:
        conn.execute("INSERT INTO people (name, birthday) VALUES ('Alice', ?)", (f"1999-{mm_dd}",))
        conn.execute("INSERT INTO people (name, birthday) VALUES ('Bob', ?)", (mm_dd,))
        conn.execute("INSERT INTO people (name, birthday) VALUES ('Charlie', ?)", (other,))
        conn.execute("INSERT INTO people (name) VALUES ('Sans-date')")

    found = {p["name"] for p in get_todays_birthdays()}
    assert found == {"Alice", "Bob"}

    first = check_birthdays()
    assert {p["name"] for p in first} == {"Alice", "Bob"}
    # rejouer le job le même jour ne renotifie pas
    assert check_birthdays() == []

    from database import get_unread_notifications
    titles = [n["title"] for n in get_unread_notifications(20)]
    assert sum("Anniversaire" in t for t in titles) == 2
    # l'âge d'Alice est calculé depuis l'année
    contents = " ".join(str(n.get("content")) for n in get_unread_notifications(20))
    assert f"{datetime.now().year - 1999} ans" in contents


# ── Pause café ───────────────────────────────────────────────

def _seed_screen(conn, minutes_ago_list):
    now = datetime.now()
    for m in minutes_ago_list:
        ts = (now - timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO screen_activity (device, app, created_at) VALUES ('mac', 'code', ?)", (ts,))


def test_continuous_minutes_gap_resets():
    from scripts.rituals import _continuous_screen_minutes

    base = datetime(2026, 7, 9, 10, 0, 0)
    rows = [(base + timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S")
            for m in [0, 5, 10, 40, 45, 50]]  # trou de 30 min au milieu
    # gap 15 min → la session courante repart à +40 → 10 minutes continues
    assert _continuous_screen_minutes(rows, gap_minutes=15) == 10.0
    # gap 60 min → tout est une seule session de 50 min
    assert _continuous_screen_minutes(rows, gap_minutes=60) == 50.0
    assert _continuous_screen_minutes([], 15) == 0.0


def test_coffee_break_alert_and_cooldown(tmp_db, monkeypatch):
    from database import get_db
    from scripts.rituals import check_coffee_break

    monkeypatch.setattr("config.BREAK_ALERT_MINUTES", 90)
    monkeypatch.setattr("config.BREAK_GAP_MINUTES", 15)
    monkeypatch.setattr("config.BREAK_COOLDOWN_MINUTES", 90)
    with get_db() as conn:
        _seed_screen(conn, range(0, 101, 5))  # 100 min d'activité continue

    r = check_coffee_break()
    assert r is not None and r["continuous_minutes"] >= 90
    # cooldown : pas de deuxième alerte immédiate
    assert check_coffee_break() is None


def test_coffee_break_none_when_pause_taken(tmp_db, monkeypatch):
    from database import get_db
    from scripts.rituals import check_coffee_break

    monkeypatch.setattr("config.BREAK_ALERT_MINUTES", 90)
    with get_db() as conn:
        # 50 min récentes, puis un trou de 40 min, puis de l'activité ancienne
        _seed_screen(conn, list(range(0, 51, 5)) + list(range(90, 140, 5)))
    assert check_coffee_break() is None


def test_coffee_break_disabled(tmp_db, monkeypatch):
    from scripts.rituals import check_coffee_break

    monkeypatch.setattr("config.BREAK_ALERT_MINUTES", 0)
    assert check_coffee_break() is None


# ── Roast / debrief / citation (LLM mocké) ───────────────────

@pytest.mark.asyncio
async def test_daily_roast_stores_and_notifies(tmp_db):
    from database import get_daily_ritual, get_db, get_unread_notifications
    from scripts import rituals

    with get_db() as conn:
        conn.execute(
            "INSERT INTO tasks (title, status, due_date) VALUES ('rendre le dossier', 'todo', '2020-01-01 10:00')")

    with patch.object(rituals.llm, "chat", new=AsyncMock(
            return_value={"content": "Le dossier attend depuis 2020, Monsieur."})):
        r = await rituals.daily_roast()

    assert "dossier" in r["roast"]
    assert r["overdue"] == 1
    row = get_daily_ritual(datetime.now().strftime("%Y-%m-%d"))
    assert row["roast"] == "Le dossier attend depuis 2020, Monsieur."
    assert any(n["title"] == "Roast du jour" for n in get_unread_notifications(10))


@pytest.mark.asyncio
async def test_daily_roast_fallback_without_llm(tmp_db):
    from scripts import rituals

    with patch.object(rituals.llm, "chat", new=AsyncMock(side_effect=RuntimeError("down"))):
        r = await rituals.daily_roast()
    # pas de tâches → phrase fixe sans LLM ; avec tâches + LLM down → fallback chiffré
    assert r["roast"]


@pytest.mark.asyncio
async def test_evening_debrief_persists_score(tmp_db):
    from database import get_daily_ritual
    from scripts import rituals

    with patch.object(rituals.llm, "chat", new=AsyncMock(
            return_value={"content": "Journée correcte, Monsieur."})):
        r = await rituals.evening_debrief()

    assert r["debrief"] == "Journée correcte, Monsieur."
    row = get_daily_ritual(datetime.now().strftime("%Y-%m-%d"))
    assert row["debrief"] and row["productivity_score"] is not None


@pytest.mark.asyncio
async def test_daily_quote_fallback_and_cache(tmp_db):
    from scripts import rituals

    with patch.object(rituals.llm, "chat", new=AsyncMock(side_effect=RuntimeError("down"))):
        r1 = await rituals.daily_quote()
    assert r1["quote"] in rituals._FALLBACK_QUOTES and r1["cached"] is False
    # deuxième appel le même jour → cache, zéro LLM
    r2 = await rituals.daily_quote()
    assert r2["cached"] is True and r2["quote"] == r1["quote"]


# ── Easter eggs ──────────────────────────────────────────────

def test_easter_eggs_match_accent_and_case():
    from agents import easter_eggs

    r = easter_eggs.match("Jarvis, je suis Iron Man")
    assert r and "armure" in r["response"] and r["emotion"] == "amused"
    assert easter_eggs.match("AUTODESTRUCTION immédiate")["emotion"] == "serious"
    assert easter_eggs.match("Es-tu vivant ?") is not None


def test_easter_eggs_no_false_positive():
    from agents import easter_eggs

    assert easter_eggs.match("quel temps fait-il demain à Lille") is None
    assert easter_eggs.match("") is None
    # message long → pas d'easter egg même si la gâchette apparaît
    long_msg = "chante " + "et explique moi la théorie des cordes " * 3
    assert easter_eggs.match(long_msg) is None


# ── Priorité emails urgents ──────────────────────────────────

def test_email_priority_mapping():
    from scripts.email_watcher import _priority_for

    assert _priority_for("payment", True) == "urgent"
    assert _priority_for("request", True) == "urgent"
    assert _priority_for("payment", False) == "high"
    assert _priority_for("payment", None) == "high"
    assert _priority_for("request", False) == "medium"

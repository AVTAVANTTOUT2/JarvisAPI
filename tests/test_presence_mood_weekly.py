"""Tests : présence par le son, mood tracking discret, debrief hebdo vocal."""

from __future__ import annotations

import json
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
    monkeypatch.setattr("config.RITUALS_TTS", False)
    from database import init_db

    init_db()
    return db_path


# ── Présence par le son ──────────────────────────────────────

def test_presence_arrival_then_departure_after_timeout(tmp_db, monkeypatch):
    from scripts.presence import PresenceDetector, get_today_sessions

    monkeypatch.setattr("config.PRESENCE_ENABLED", True)
    monkeypatch.setattr("config.PRESENCE_TIMEOUT_MIN", 60)
    det = PresenceDetector()
    t0 = datetime.now().timestamp()

    # premier son → arrivée (une seule fois)
    assert det.on_sound(t0) == "arrived"
    assert det.on_sound(t0 + 5) is None
    assert det.present is True

    # 30 min de silence → toujours présent
    assert det.tick(t0 + 30 * 60) is None
    assert det.present is True

    # du bruit à +40 min relance le compteur
    det.on_sound(t0 + 40 * 60)
    assert det.tick(t0 + 95 * 60) is None          # 55 min après le dernier son
    assert det.tick(t0 + 101 * 60) == "left"       # 61 min → départ
    assert det.present is False

    sessions = get_today_sessions()
    assert len(sessions) == 1
    s = sessions[0]
    assert s["left_at"] is not None
    assert s["duration_min"] == pytest.approx(40.0, abs=0.5)

    # nouveau son après le départ → nouvelle session
    assert det.on_sound(t0 + 200 * 60) == "arrived"
    assert len(get_today_sessions()) == 2


def test_presence_disabled(tmp_db, monkeypatch):
    from scripts.presence import PresenceDetector

    monkeypatch.setattr("config.PRESENCE_ENABLED", False)
    det = PresenceDetector()
    assert det.on_sound() is None
    assert det.present is False
    assert det.tick() is None


def test_presence_status_payload(tmp_db, monkeypatch):
    from scripts.presence import PresenceDetector

    monkeypatch.setattr("config.PRESENCE_ENABLED", True)
    det = PresenceDetector()
    st = det.get_status()
    assert st["present"] is False and st["last_sound_ago_s"] is None
    det.on_sound()
    st = det.get_status()
    assert st["present"] is True and st["arrived_at"] is not None


# ── Mood tracking discret ────────────────────────────────────

def _seed_messages(conn, day: str, count: int, role: str = "user"):
    conn.execute("INSERT OR IGNORE INTO conversations (id, agent) VALUES (1, 'orchestrator')")
    for i in range(count):
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (1, ?, 'x', ?)",
            (role, f"{day} 10:{i % 60:02d}:00"),
        )


def test_mood_signal_deviation_and_flags(tmp_db):
    from database import get_db, get_mood_signals
    from scripts.rituals import compute_mood_signal

    today = datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        # 14 jours d'historique à ~10 messages/jour
        for d in range(1, 15):
            day = (datetime.now().date() - timedelta(days=d)).isoformat()
            _seed_messages(conn, day, 10)
        # aujourd'hui : 20 messages (+100 %) + activité nocturne
        _seed_messages(conn, today, 20)
        for i in range(12):
            conn.execute(
                "INSERT INTO screen_activity (device, app, created_at) VALUES ('mac', 'code', ?)",
                (f"{today} 23:{i * 4:02d}:00",))

    sig = compute_mood_signal()
    assert sig["msg_count"] == 20
    assert sig["msg_avg_14d"] == pytest.approx(10.0)
    assert sig["deviation_pct"] == pytest.approx(100.0)
    assert "hyperactivite" in sig["flags"]
    assert "activite_nocturne" in sig["flags"]

    # persisté + relisible ; rejouer le job = UPSERT, pas de doublon
    compute_mood_signal()
    rows = get_mood_signals(14)
    assert len([r for r in rows if r["date"] == today]) == 1
    assert json.loads(rows[0]["flags"]) == sig["flags"]

    # notification discrète : priorité low, formulée en observation, une seule
    from database import get_unread_notifications
    notifs = [n for n in get_unread_notifications(20) if n["title"].startswith("Signal du jour")]
    assert len(notifs) == 1
    assert notifs[0]["priority"] == "low"
    assert "pas un diagnostic" in notifs[0]["content"]


def test_mood_signal_quiet_day_no_flags(tmp_db):
    from database import get_db, get_unread_notifications
    from scripts.rituals import compute_mood_signal

    today = datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        _seed_messages(conn, today, 3)  # pas d'historique → pas de déviation

    sig = compute_mood_signal()
    assert sig["flags"] == []
    assert sig["deviation_pct"] is None
    assert not any(n["title"].startswith("Signal du jour") for n in get_unread_notifications(10))


def test_mood_signal_silence_flag(tmp_db):
    from database import get_db
    from scripts.rituals import compute_mood_signal

    today = datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        for d in range(1, 15):
            day = (datetime.now().date() - timedelta(days=d)).isoformat()
            _seed_messages(conn, day, 10)
        _seed_messages(conn, today, 2)  # -80 %

    sig = compute_mood_signal()
    assert "silence_inhabituel" in sig["flags"]


# ── Debrief hebdo vocal ──────────────────────────────────────

@pytest.mark.asyncio
async def test_weekly_debrief_stores_and_notifies(tmp_db):
    from database import get_daily_ritual, get_db, get_unread_notifications
    from scripts import rituals

    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO tasks (title, status, completed_at) VALUES ('dossier BTS', 'done', ?)",
            (today,))

    with patch.object(rituals.llm, "chat", new=AsyncMock(
            return_value={"content": "Semaine correcte, Monsieur. Cap sur le dossier."})):
        r = await rituals.weekly_debrief()

    assert r["weekly_debrief"].startswith("Semaine correcte")
    assert r["score"] == 58  # 50 + 8×1
    row = get_daily_ritual(datetime.now().strftime("%Y-%m-%d"))
    assert row["weekly_debrief"] == r["weekly_debrief"]
    assert any(n["title"] == "Debrief de la semaine" for n in get_unread_notifications(10))


@pytest.mark.asyncio
async def test_weekly_debrief_fallback_without_llm(tmp_db):
    from scripts import rituals

    with patch.object(rituals.llm, "chat", new=AsyncMock(side_effect=RuntimeError("down"))):
        r = await rituals.weekly_debrief()
    assert "Semaine close" in r["weekly_debrief"]
    assert "sur 100" in r["weekly_debrief"]

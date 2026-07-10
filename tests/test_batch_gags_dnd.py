"""Tests : running gags, binge streaming, réunions, promesses, DND, comparatif, retour tardif."""

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
    monkeypatch.setattr("config.RITUALS_TTS", False)
    from database import init_db

    init_db()
    return db_path


# ── 10. Running gags par contact ─────────────────────────────

def test_running_gags_add_dedupe_cap(tmp_db):
    from database import add_running_gag, get_db, get_running_gags

    with get_db() as conn:
        cur = conn.execute("INSERT INTO people (name) VALUES ('Léo')")
        pid = cur.lastrowid

    assert add_running_gag(pid, "le surnom Capitaine Retard") is True
    assert add_running_gag(pid, "LE SURNOM capitaine retard") is False  # dédup insensible casse
    assert add_running_gag(pid, "") is False
    for i in range(20):
        add_running_gag(pid, f"gag {i}")
    gags = get_running_gags(pid)
    assert len(gags) == 15          # cap FIFO
    assert "gag 19" in gags and "le surnom Capitaine Retard" not in gags
    assert get_running_gags(999999) == []


# ── 11. Binge streaming ──────────────────────────────────────

def _seed_screen_app(conn, app: str, minutes_ago_list):
    now = datetime.now()
    for m in minutes_ago_list:
        ts = (now - timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO screen_activity (device, app, created_at) VALUES ('mac', ?, ?)", (app, ts))


def test_binge_detected_only_for_streaming_apps(tmp_db, monkeypatch):
    from database import get_db, get_unread_notifications
    from scripts.rituals import check_streaming_binge

    monkeypatch.setattr("config.BINGE_ALERT_MINUTES", 120)
    with get_db() as conn:
        _seed_screen_app(conn, "VS Code", range(0, 131, 5))  # 130 min de code
    assert check_streaming_binge() is None                    # pas du streaming

    with get_db() as conn:
        _seed_screen_app(conn, "Netflix", range(0, 131, 5))
    r = check_streaming_binge()
    assert r is not None and r["continuous_minutes"] >= 120
    notifs = [n for n in get_unread_notifications(10) if n["title"] == "Marathon streaming"]
    assert len(notifs) == 1 and "Je ne juge pas" in notifs[0]["content"]
    # cooldown 4h
    assert check_streaming_binge() is None


def test_binge_disabled(tmp_db, monkeypatch):
    from scripts.rituals import check_streaming_binge

    monkeypatch.setattr("config.BINGE_ALERT_MINUTES", 0)
    assert check_streaming_binge() is None


# ── 12. Réunions ─────────────────────────────────────────────

def test_meeting_tracker_opens_and_closes(tmp_db, monkeypatch):
    from scripts.meeting import MeetingTracker

    monkeypatch.setattr("config.MEETING_CAPTURE_ENABLED", True)
    monkeypatch.setattr("config.MEETING_MIN_SPEECH_S", 60)
    monkeypatch.setattr("config.MEETING_SILENCE_MIN", 10)
    tr = MeetingTracker()
    t0 = datetime.now().timestamp()

    # 5 prises de parole de 15 s → ouverture au 60e cumulé
    events = [tr.add_utterance(f"phrase {i}", 15, now=t0 + i * 30) for i in range(5)]
    assert "started" in events
    assert tr.active is True
    assert tr.tick(now=t0 + 5 * 30 + 60) is None       # 1 min de silence — trop tôt

    meeting = tr.tick(now=t0 + 5 * 30 + 11 * 60)        # 11 min de silence → clôture
    assert meeting is not None
    assert meeting["utterances"] == 5
    assert "phrase 3" in meeting["transcript"]
    assert tr.active is False and tr.buffer == []


def test_meeting_tracker_disabled_and_window(tmp_db, monkeypatch):
    from scripts.meeting import MeetingTracker

    monkeypatch.setattr("config.MEETING_CAPTURE_ENABLED", False)
    tr = MeetingTracker()
    assert tr.add_utterance("bonjour à tous", 20) is None

    monkeypatch.setattr("config.MEETING_CAPTURE_ENABLED", True)
    monkeypatch.setattr("config.MEETING_MIN_SPEECH_S", 60)
    monkeypatch.setattr("config.MEETING_WINDOW_MIN", 15)
    tr = MeetingTracker()
    t0 = datetime.now().timestamp()
    # 40 s de parole… puis 20 min de trou → la fenêtre purge, pas d'ouverture
    tr.add_utterance("un", 40, now=t0)
    assert tr.add_utterance("deux", 40, now=t0 + 20 * 60) is None
    assert tr.active is False


@pytest.mark.asyncio
async def test_meeting_summary_creates_tasks_and_recording(tmp_db, monkeypatch):
    from database import get_db, get_tasks, get_unread_notifications
    from scripts import meeting as mt

    meeting = {
        "started_at": "2026-07-09 14:00:00", "ended_at": "2026-07-09 14:45:00",
        "duration_seconds": 2700, "utterances": 30,
        "transcript": "On valide le budget. Nolann enverra le devis à Karim vendredi.",
    }
    with patch.object(mt.llm, "chat", new=AsyncMock(return_value={"content": (
            '{"title": "Point budget", "summary": "Budget validé.", '
            '"actions": [{"title": "Envoyer le devis à Karim", "due_hint": "vendredi"}]}')})):
        r = await mt.summarize_meeting(meeting)

    assert r["title"] == "Point budget"
    tasks = get_tasks()
    assert any(t["title"] == "Envoyer le devis à Karim" for t in tasks)
    with get_db() as conn:
        row = conn.execute("SELECT title, label FROM recordings").fetchone()
    assert row["label"] == "réunion" and row["title"] == "Point budget"
    assert any(n["title"] == "Point budget" for n in get_unread_notifications(10))


# ── 13. Promesses non tenues ─────────────────────────────────

@pytest.mark.asyncio
async def test_commitments_extraction_and_dedupe(tmp_db):
    from database import get_commitments, get_db
    from scripts import commitments as cm

    today = datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        conn.execute("INSERT INTO conversations (id, agent) VALUES (1, 'orchestrator')")
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "VALUES (1, 'user', 'je promets d''envoyer le dossier à Karim demain', ?)",
            (f"{today} 11:00:00",))

    payload = '[{"content": "Envoyer le dossier à Karim", "made_to": "Karim", "due_hint": "demain"}]'
    with patch.object(cm.llm, "chat", new=AsyncMock(return_value={"content": payload})):
        added = await cm.extract_today_commitments()
        assert len(added) == 1
        # rejouer : dédup sur contenu ouvert
        assert await cm.extract_today_commitments() == []

    assert len(get_commitments("open")) == 1


def test_commitments_overdue_notification(tmp_db):
    from database import add_commitment, get_db, get_unread_notifications, update_commitment_status
    from scripts.commitments import check_overdue_commitments_job

    cid = add_commitment("Rappeler le garagiste", made_to="garagiste")
    with get_db() as conn:  # vieillit l'engagement de 5 jours
        conn.execute(
            "UPDATE commitments SET created_at = datetime('now', '-5 days') WHERE id = ?", (cid,))

    r = check_overdue_commitments_job()
    assert r == {"overdue": 1}
    assert check_overdue_commitments_job() is None  # dédup du jour
    notifs = [n for n in get_unread_notifications(10) if n["title"].startswith("Promesses")]
    assert "garagiste" in notifs[0]["content"]

    # marqué tenu → plus en souffrance
    assert update_commitment_status(cid, "kept") is True
    from database import get_overdue_commitments
    assert get_overdue_commitments(3) == []


# ── 14. DND « silence total sauf feu » ───────────────────────

def test_dnd_lifecycle(tmp_db):
    from database import clear_dnd, get_dnd_status, is_dnd_active, set_dnd

    assert is_dnd_active() is False
    until = set_dnd(120)
    assert is_dnd_active() is True
    assert get_dnd_status() == {"active": True, "until": until}
    clear_dnd()
    assert is_dnd_active() is False


def test_dnd_expires(tmp_db):
    from database import is_dnd_active, set_setting

    past = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
    set_setting("dnd_until", past)
    assert is_dnd_active() is False


def test_dnd_silences_rituals_speak(tmp_db, monkeypatch):
    from database import set_dnd
    from scripts import rituals

    monkeypatch.setattr("config.RITUALS_TTS", True)
    set_dnd(60)
    called = []
    monkeypatch.setattr(
        "scripts.jarvis_daemon.daemon",
        type("D", (), {"tts_queue": type("Q", (), {"put_nowait": lambda s, x: called.append(x)})()})(),
        raising=False,
    )
    rituals._speak("test", "neutral")
    assert called == []  # rien ne part pendant le DND


# ── 15. Comparatif toi vs toi ────────────────────────────────

def test_week_comparison_deltas(tmp_db):
    from database import get_db, get_week_comparison

    with get_db() as conn:
        conn.execute("INSERT INTO conversations (id, agent) VALUES (1, 'orchestrator')")
        today = datetime.now().date()
        # semaine courante : 4 messages ; précédente : 2
        for d, n in ((1, 4), (10, 2)):
            day = (today - timedelta(days=d)).isoformat()
            for i in range(n):
                conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) "
                    "VALUES (1, 'user', 'x', ?)", (f"{day} 10:0{i}:00",))
        # tâches : 2 cette semaine, 1 la précédente
        conn.execute("INSERT INTO tasks (title, status, completed_at) VALUES ('a', 'done', ?)",
                     ((today - timedelta(days=2)).isoformat() + " 10:00:00",))
        conn.execute("INSERT INTO tasks (title, status, completed_at) VALUES ('b', 'done', ?)",
                     ((today - timedelta(days=3)).isoformat() + " 10:00:00",))
        conn.execute("INSERT INTO tasks (title, status, completed_at) VALUES ('c', 'done', ?)",
                     ((today - timedelta(days=9)).isoformat() + " 10:00:00",))

    r = get_week_comparison()
    assert r["this_week"]["messages"] == 4 and r["last_week"]["messages"] == 2
    assert r["deltas_pct"]["messages"] == 100.0
    assert r["this_week"]["tasks_done"] == 2 and r["last_week"]["tasks_done"] == 1
    assert r["deltas_pct"]["tasks_done"] == 100.0


# ── 16. Retour tardif ────────────────────────────────────────

def _seed_location(conn, place_name: str | None, minutes_ago: int = 2, category: str = "leisure"):
    ts = (datetime.now() - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    place_id = None
    if place_name:
        cur = conn.execute(
            "INSERT INTO places (name, category, latitude, longitude) VALUES (?, ?, 50.6, 3.0)",
            (place_name, category))
        place_id = cur.lastrowid
    conn.execute(
        "INSERT INTO location_history (latitude, longitude, place_id, created_at) VALUES (50.6, 3.0, ?, ?)",
        (place_id, ts))


def test_late_return_alert_away_after_hour(tmp_db, monkeypatch):
    from database import get_db, get_unread_notifications
    from scripts.rituals import check_late_return

    monkeypatch.setattr("config.LATE_RETURN_ENABLED", True)
    monkeypatch.setattr("config.LATE_RETURN_HOUR", 23)
    with get_db() as conn:
        _seed_location(conn, "Bar Le Comptoir")

    late = datetime.now().replace(hour=23, minute=30)
    r = check_late_return(now=late)
    assert r is not None and r["place"] == "Bar Le Comptoir"
    notifs = [n for n in get_unread_notifications(10) if n["title"].startswith("Retour tardif")]
    assert "Rentrez" in notifs[0]["content"]
    # une seule alerte par nuit (y compris après minuit)
    assert check_late_return(now=late.replace(hour=23, minute=59)) is None


def test_late_return_skips_home_early_and_dnd(tmp_db, monkeypatch):
    from database import get_db, set_dnd
    from scripts.rituals import check_late_return

    monkeypatch.setattr("config.LATE_RETURN_ENABLED", True)
    with get_db() as conn:
        _seed_location(conn, "Appart Lille", category="home")
    late = datetime.now().replace(hour=23, minute=30)
    assert check_late_return(now=late) is None            # chez lui

    with get_db() as conn:
        conn.execute("DELETE FROM location_history"); conn.execute("DELETE FROM places")
        _seed_location(conn, "Bar")
    assert check_late_return(now=datetime.now().replace(hour=20, minute=0)) is None  # trop tôt

    set_dnd(60)
    assert check_late_return(now=late) is None            # silence total

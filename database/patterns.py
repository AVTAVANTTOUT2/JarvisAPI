"""Humeurs, patterns, messages quotidiens et briefings."""

from __future__ import annotations

import json
from datetime import datetime

from jarvis.event_bus import event_bus
from jarvis.events import PatternDetected

from .core import get_db


def create_pattern(pattern_type: str, description: str) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO patterns (pattern_type, description) VALUES (?, ?)",
            (pattern_type, description),
        )
        pattern_id = int(cur.lastrowid)
    event_bus.emit_nowait(PatternDetected(pattern_id, pattern_type, description))
    return pattern_id


def update_pattern(pattern_id: int, occurrences_increment: int = 1) -> None:
    with get_db() as conn:
        conn.execute(
            """UPDATE patterns
               SET occurrences = occurrences + ?, last_seen = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (occurrences_increment, pattern_id),
        )


def find_or_create_pattern(description: str, pattern_type: str = "behavioral") -> int:
    """Cherche un pattern par similarité simple (description identique). Sinon crée."""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM patterns WHERE LOWER(description) = LOWER(?) AND status = 'active'",
            (description,),
        ).fetchone()
        if existing:
            pattern_id = int(existing["id"])
            update_pattern(pattern_id)
            event_bus.emit_nowait(
                PatternDetected(pattern_id, pattern_type, description)
            )
            return pattern_id
        return create_pattern(pattern_type, description)


def save_mood(mood: int, energy: int, context: str = None, triggers: str = None) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO mood_log (mood_score, energy_level, context, triggers) VALUES (?, ?, ?, ?)",
            (mood, energy, context, triggers)
        )
        return cur.lastrowid


def get_recent_moods(limit: int = 14) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM mood_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_active_patterns() -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM patterns WHERE status = 'active' ORDER BY last_seen DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_daily_messages(date: str = None) -> list:
    """Récupère tous les messages d'une date (YYYY-MM-DD). Aujourd'hui par défaut.

    Utilisé par `productivity.evening_summary()` pour résumer la journée.
    """
    target = date or datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT role, content, agent, model, created_at
               FROM messages
               WHERE DATE(created_at) = ?
               ORDER BY created_at""",
            (target,),
        ).fetchall()
        return [dict(r) for r in rows]


def save_daily_briefing(date: str, morning: str = None, evening: str = None) -> None:
    """Insert ou update le briefing du jour (UPSERT sur la date)."""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id, morning_briefing, evening_summary FROM daily_briefings WHERE date = ?",
            (date,),
        ).fetchone()
        if existing:
            new_m = morning if morning is not None else existing["morning_briefing"]
            new_e = evening if evening is not None else existing["evening_summary"]
            conn.execute(
                "UPDATE daily_briefings SET morning_briefing = ?, evening_summary = ? WHERE date = ?",
                (new_m, new_e, date),
            )
        else:
            conn.execute(
                "INSERT INTO daily_briefings (date, morning_briefing, evening_summary) VALUES (?, ?, ?)",
                (date, morning, evening),
            )


def get_pattern(pattern_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM patterns WHERE id = ?", (pattern_id,)).fetchone()
        return dict(row) if row else None

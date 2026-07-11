"""Persistance de l'activité écran, des machines et sessions de travail."""

from __future__ import annotations

import secrets
from datetime import datetime

from .core import get_db


def get_device_by_id(device_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
    return dict(row) if row else None


def save_screen_activity(
    device: str,
    app: str | None,
    activity: str | None,
    mood: str | None = None,
    notable: str | None = None,
    screenshot_hash: str | None = None,
    change_pct: float | None = None,
) -> int:
    valid_moods = {"focused", "idle", "distracted", "stuck", "browsing", "unknown"}
    if mood and mood not in valid_moods:
        mood = "unknown"
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO screen_activity
               (device, app, activity, mood, notable, screenshot_hash, change_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (device, app, activity, mood, notable, screenshot_hash, change_pct),
        )
        conn.execute(
            "UPDATE devices SET last_screen_at = CURRENT_TIMESTAMP WHERE device_id = ?",
            (device,),
        )
        return int(cursor.lastrowid)


def get_screen_activity(hours: int = 24, device: str | None = None) -> list[dict]:
    with get_db() as conn:
        if device:
            rows = conn.execute(
                """SELECT * FROM screen_activity
                   WHERE created_at >= datetime('now', ?) AND device = ?
                   ORDER BY created_at DESC""",
                (f"-{int(hours)} hours", device),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM screen_activity
                   WHERE created_at >= datetime('now', ?)
                   ORDER BY created_at DESC""",
                (f"-{int(hours)} hours",),
            ).fetchall()
    return [dict(row) for row in rows]


def get_current_screen_context(device: str | None = None) -> dict | None:
    with get_db() as conn:
        if device:
            row = conn.execute(
                """SELECT * FROM screen_activity
                   WHERE device = ? AND created_at >= datetime('now', '-5 minutes')
                   ORDER BY created_at DESC LIMIT 1""",
                (device,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM screen_activity
                   WHERE created_at >= datetime('now', '-5 minutes')
                   ORDER BY created_at DESC LIMIT 1"""
            ).fetchone()
    return dict(row) if row else None


def upsert_app_usage(device: str, app: str, seconds: int) -> None:
    if not app or seconds <= 0:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        conn.execute(
            """INSERT INTO app_usage
               (device, app, date, duration_seconds, session_count)
               VALUES (?, ?, ?, ?, 1)
               ON CONFLICT(device, app, date) DO UPDATE SET
                   duration_seconds = duration_seconds + excluded.duration_seconds,
                   session_count = session_count + 1""",
            (device, app, today, int(seconds)),
        )


def get_app_usage(date: str | None = None, device: str | None = None) -> list[dict]:
    target = date or datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        if device:
            rows = conn.execute(
                """SELECT * FROM app_usage WHERE date = ? AND device = ?
                   ORDER BY duration_seconds DESC""",
                (target, device),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM app_usage WHERE date = ?
                   ORDER BY duration_seconds DESC""",
                (target,),
            ).fetchall()
    return [dict(row) for row in rows]


def get_app_usage_range(days: int = 7, device: str | None = None) -> list[dict]:
    with get_db() as conn:
        if device:
            rows = conn.execute(
                """SELECT * FROM app_usage
                   WHERE date >= date('now', ?) AND device = ?
                   ORDER BY date DESC, duration_seconds DESC""",
                (f"-{int(days)} days", device),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM app_usage WHERE date >= date('now', ?)
                   ORDER BY date DESC, duration_seconds DESC""",
                (f"-{int(days)} days",),
            ).fetchall()
    return [dict(row) for row in rows]


def register_device(
    device_id: str,
    device_name: str,
    device_type: str = "desktop",
    ip_tailscale: str | None = None,
) -> str:
    with get_db() as conn:
        row = conn.execute(
            "SELECT auth_token FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if row and row["auth_token"]:
            conn.execute(
                """UPDATE devices SET device_name = ?, device_type = ?,
                       ip_tailscale = COALESCE(?, ip_tailscale), is_online = 1,
                       last_heartbeat = CURRENT_TIMESTAMP WHERE device_id = ?""",
                (device_name, device_type, ip_tailscale, device_id),
            )
            return str(row["auth_token"])
        token = secrets.token_urlsafe(32)
        conn.execute(
            """INSERT INTO devices
               (device_id, device_name, device_type, is_online, last_heartbeat,
                ip_tailscale, auth_token)
               VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP, ?, ?)""",
            (device_id, device_name, device_type, ip_tailscale, token),
        )
    return token


def update_device_heartbeat(device_id: str) -> None:
    with get_db() as conn:
        conn.execute(
            """UPDATE devices SET last_heartbeat = CURRENT_TIMESTAMP, is_online = 1
               WHERE device_id = ?""",
            (device_id,),
        )


def set_active_device(device_id: str) -> None:
    with get_db() as conn:
        conn.execute("UPDATE devices SET is_active = 0")
        conn.execute(
            "UPDATE devices SET is_active = 1 WHERE device_id = ?", (device_id,)
        )


def get_active_device() -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM devices WHERE is_active = 1 LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_all_devices() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM devices ORDER BY is_active DESC, last_heartbeat DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def mark_device_offline(device_id: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE devices SET is_online = 0 WHERE device_id = ?", (device_id,)
        )


def start_work_session(device: str, app: str) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO work_sessions (device, app, started_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)""",
            (device, app),
        )
        return int(cursor.lastrowid)


def end_work_session(session_id: int, description: str | None = None) -> None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT started_at FROM work_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return
        try:
            started = datetime.fromisoformat(row["started_at"].replace("Z", ""))
            duration = (datetime.now() - started).total_seconds() / 60.0
        except (AttributeError, TypeError, ValueError):
            duration = None
        conn.execute(
            """UPDATE work_sessions SET ended_at = CURRENT_TIMESTAMP,
                   duration_min = ?, description = COALESCE(?, description)
               WHERE id = ?""",
            (duration, description, session_id),
        )


def get_work_sessions(days: int = 7) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM work_sessions
               WHERE started_at >= datetime('now', ?)
               ORDER BY started_at DESC""",
            (f"-{int(days)} days",),
        ).fetchall()
    return [dict(row) for row in rows]

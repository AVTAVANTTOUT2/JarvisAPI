"""Persistance de l'activité écran, des machines et sessions de travail."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .core import get_db


def get_device_by_id(device_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            """SELECT id, device_id, device_name, device_type, is_active,
                      is_online, last_heartbeat, last_screen_at, ip_tailscale,
                      token_hash, revoked, paired_at, token_rotated_at, created_at
               FROM devices WHERE device_id = ?""",
            (device_id,),
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


def create_device_pairing_code(code_hash: str, expires_at: str) -> None:
    with get_db() as conn:
        conn.execute(
            """DELETE FROM device_pairing_codes
               WHERE used_at IS NOT NULL OR datetime(expires_at) <= datetime('now')"""
        )
        conn.execute(
            "INSERT INTO device_pairing_codes (code_hash, expires_at) VALUES (?, ?)",
            (code_hash, expires_at),
        )


def consume_device_pairing_code(
    code_hash: str,
    client_key: str,
    *,
    max_attempts: int,
    window_minutes: int,
    lockout_minutes: int,
) -> tuple[str, int]:
    """Consomme un code valide et limite les essais par client.

    Retourne ``("ok", 0)``, ``("invalid", 0)`` ou
    ``("blocked", retry_after_seconds)``.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    max_attempts = max(1, int(max_attempts))
    window = timedelta(minutes=max(1, int(window_minutes)))
    lockout = timedelta(minutes=max(1, int(lockout_minutes)))

    with get_db() as conn:
        attempt = conn.execute(
            """SELECT failed_attempts, window_started_at, blocked_until
               FROM device_pairing_attempts WHERE client_key = ?""",
            (client_key,),
        ).fetchone()
        if attempt and attempt["blocked_until"]:
            try:
                blocked_until = datetime.fromisoformat(str(attempt["blocked_until"]))
            except ValueError:
                blocked_until = now
            if blocked_until > now:
                return "blocked", max(1, int((blocked_until - now).total_seconds()))

        cursor = conn.execute(
            """UPDATE device_pairing_codes
               SET used_at = CURRENT_TIMESTAMP
               WHERE code_hash = ? AND used_at IS NULL
                 AND datetime(expires_at) > datetime('now')""",
            (code_hash,),
        )
        if cursor.rowcount == 1:
            conn.execute(
                "DELETE FROM device_pairing_attempts WHERE client_key = ?",
                (client_key,),
            )
            return "ok", 0

        attempts = 0
        window_started = now
        if attempt:
            try:
                previous_window = datetime.fromisoformat(str(attempt["window_started_at"]))
            except ValueError:
                previous_window = now
            if now - previous_window < window:
                attempts = int(attempt["failed_attempts"] or 0)
                window_started = previous_window
        attempts += 1

        blocked_until_value: str | None = None
        status = "invalid"
        retry_after = 0
        if attempts >= max_attempts:
            blocked_until = now + lockout
            blocked_until_value = blocked_until.isoformat(timespec="seconds")
            status = "blocked"
            retry_after = max(1, int(lockout.total_seconds()))

        conn.execute(
            """INSERT INTO device_pairing_attempts
                   (client_key, failed_attempts, window_started_at, blocked_until)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(client_key) DO UPDATE SET
                   failed_attempts = excluded.failed_attempts,
                   window_started_at = excluded.window_started_at,
                   blocked_until = excluded.blocked_until""",
            (
                client_key,
                attempts,
                window_started.isoformat(timespec="seconds"),
                blocked_until_value,
            ),
        )
        return status, retry_after


def register_local_device(
    device_id: str,
    device_name: str,
    device_type: str = "desktop",
    ip_tailscale: str | None = None,
) -> None:
    """Enregistre la machine serveur sans lui émettre de jeton distant."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO devices
                   (device_id, device_name, device_type, is_online,
                    last_heartbeat, ip_tailscale)
               VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP, ?)
               ON CONFLICT(device_id) DO UPDATE SET
                   device_name = excluded.device_name,
                   device_type = excluded.device_type,
                   ip_tailscale = COALESCE(excluded.ip_tailscale, devices.ip_tailscale),
                   is_online = 1,
                   last_heartbeat = CURRENT_TIMESTAMP""",
            (device_id, device_name, device_type, ip_tailscale),
        )


def register_remote_device(
    device_id: str,
    device_name: str,
    token_hash: str,
    device_type: str = "desktop",
    ip_tailscale: str | None = None,
) -> bool:
    """Crée un appareil distant. Un identifiant existant n'est jamais ré-enrôlé."""
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO devices
                   (device_id, device_name, device_type, is_online,
                    last_heartbeat, ip_tailscale, token_hash, revoked, paired_at)
               VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP, ?, ?, 0, CURRENT_TIMESTAMP)""",
            (device_id, device_name, device_type, ip_tailscale, token_hash),
        )
    return cursor.rowcount == 1


def rotate_device_token(device_id: str, token_hash: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            """UPDATE devices
               SET token_hash = ?, revoked = 0, token_rotated_at = CURRENT_TIMESTAMP,
                   paired_at = COALESCE(paired_at, CURRENT_TIMESTAMP)
               WHERE device_id = ?""",
            (token_hash, device_id),
        )
    return cursor.rowcount == 1


def revoke_device(device_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            """UPDATE devices
               SET token_hash = NULL, revoked = 1, is_online = 0
               WHERE device_id = ? AND revoked = 0""",
            (device_id,),
        )
    return cursor.rowcount == 1


def update_device_heartbeat(device_id: str) -> None:
    with get_db() as conn:
        conn.execute(
            """UPDATE devices SET last_heartbeat = CURRENT_TIMESTAMP, is_online = 1
               WHERE device_id = ?""",
            (device_id,),
        )


def set_active_device(device_id: str) -> bool:
    """Active un appareil enrôlé sans modifier l'état si la cible est absente."""
    with get_db() as conn:
        target = conn.execute(
            """SELECT 1 FROM devices
               WHERE device_id = ? AND COALESCE(revoked, 0) = 0""",
            (device_id,),
        ).fetchone()
        if not target:
            return False
        conn.execute("UPDATE devices SET is_active = 0")
        cursor = conn.execute(
            """UPDATE devices SET is_active = 1
               WHERE device_id = ? AND COALESCE(revoked, 0) = 0""",
            (device_id,),
        )
        return cursor.rowcount == 1


def get_active_device() -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            """SELECT id, device_id, device_name, device_type, is_active,
                      is_online, last_heartbeat, last_screen_at, ip_tailscale,
                      revoked, paired_at, token_rotated_at, created_at
               FROM devices WHERE is_active = 1 LIMIT 1"""
        ).fetchone()
    return dict(row) if row else None


def get_all_devices() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, device_id, device_name, device_type, is_active,
                      is_online, last_heartbeat, last_screen_at, ip_tailscale,
                      revoked, paired_at, token_rotated_at, created_at
               FROM devices ORDER BY is_active DESC, last_heartbeat DESC"""
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

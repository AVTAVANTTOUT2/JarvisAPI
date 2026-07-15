"""Persistance des sessions d'authentification locales."""

from __future__ import annotations

from .core import get_db


def create_session_row(
    token_hash: str,
    expires_at: str,
    user_agent: str = "",
    ip: str = "",
    mobile_device_id: str | None = None,
) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO sessions (token_hash, expires_at, user_agent, ip, mobile_device_id)
               VALUES (?, ?, ?, ?, ?)""",
            (token_hash, expires_at, user_agent, ip, mobile_device_id),
        )
        return int(cursor.lastrowid)


def get_session_by_token_hash(token_hash: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE token_hash = ? AND revoked = 0",
            (token_hash,),
        ).fetchone()
    return dict(row) if row else None


def touch_session(token_hash: str, new_expires_at: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE sessions SET last_seen_at = CURRENT_TIMESTAMP, expires_at = ? WHERE token_hash = ?",
            (new_expires_at, token_hash),
        )


def revoke_session_by_token_hash(token_hash: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE sessions SET revoked = 1 WHERE token_hash = ? AND revoked = 0",
            (token_hash,),
        )
    return cursor.rowcount > 0


def revoke_session_by_id(session_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE sessions SET revoked = 1 WHERE id = ? AND revoked = 0",
            (session_id,),
        )
    return cursor.rowcount > 0


def revoke_all_sessions(except_token_hash: str | None = None) -> None:
    with get_db() as conn:
        if except_token_hash:
            conn.execute(
                "UPDATE sessions SET revoked = 1 WHERE revoked = 0 AND token_hash != ?",
                (except_token_hash,),
            )
        else:
            conn.execute("UPDATE sessions SET revoked = 1 WHERE revoked = 0")


def list_active_sessions() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, created_at, expires_at, last_seen_at, user_agent, ip, mobile_device_id
               FROM sessions
               WHERE revoked = 0 AND datetime(expires_at) > datetime('now')
               ORDER BY last_seen_at DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def purge_expired_sessions() -> int:
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM sessions WHERE revoked = 1 OR datetime(expires_at) <= datetime('now')"
        )
    return cursor.rowcount

"""Persistance des abonnements Web Push."""

from __future__ import annotations

from .core import get_db


def upsert_push_subscription(
    endpoint: str, p256dh: str, auth: str, user_agent: str = ""
) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO push_subscriptions (endpoint, p256dh, auth, user_agent)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(endpoint) DO UPDATE SET
                   p256dh = excluded.p256dh,
                   auth = excluded.auth""",
            (endpoint, p256dh, auth, user_agent),
        )
        return int(cursor.lastrowid)


def delete_push_subscription(endpoint: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
        )
    return cursor.rowcount > 0


def get_all_push_subscriptions() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM push_subscriptions").fetchall()
    return [dict(row) for row in rows]

"""Notifications non lues — lecture SQLite de la table notifications.

Retourne les alertes/notifications non lues triées par priorité.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import config as cfg

logger = logging.getLogger(__name__)

PRIORITY_COLORS: dict[str, str] = {
    "urgent": "#ff0040",   # rouge
    "high": "#ff0040",
    "medium": "#ffb000",   # ambre
    "low": "#00ff41",      # vert
}

PRIORITY_ICONS: dict[str, str] = {
    "urgent": "\uD83D\uDD34",   # 🔴
    "high": "\uD83D\uDD34",
    "medium": "\uD83D\uDFE1",   # 🟡
    "low": "\uD83D\uDFE2",      # 🟢
}


def get_unread_notifications() -> list[dict[str, Any]]:
    """Retourne les notifications non lues depuis SQLite."""
    db_path = _resolve_db_path()
    if not db_path:
        return []

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """SELECT id, source, title, content, priority, read, created_at
               FROM notifications
               WHERE read = 0 OR read IS NULL
               ORDER BY
                 CASE priority
                   WHEN 'urgent' THEN 1
                   WHEN 'high' THEN 2
                   WHEN 'medium' THEN 3
                   WHEN 'low' THEN 4
                   ELSE 5
                 END,
                 created_at DESC
               LIMIT ?""",
            (cfg.MAX_NOTIFICATIONS,),
        )
        rows = cur.fetchall()
        conn.close()
    except sqlite3.OperationalError as exc:
        logger.warning("Table notifications indisponible: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for row in rows:
        priority = (row["priority"] or "low").lower()
        color = PRIORITY_COLORS.get(priority, "#888888")
        icon = PRIORITY_ICONS.get(priority, "\u25CF")
        title = (row["title"] or "").strip()[:60]
        content = (row["content"] or "").strip()[:80]
        display = title or content or "Notification"
        source = row["source"] or "system"

        results.append({
            "id": row["id"],
            "source": source,
            "content": display,
            "priority": priority,
            "priority_color": color,
            "priority_icon": icon,
            "is_urgent": priority in ("urgent", "high"),
        })

    return results


def _resolve_db_path() -> str | None:
    root = Path(__file__).resolve().parent.parent.parent
    db_full = root / "data" / "jarvis.db"
    if db_full.exists():
        return str(db_full)
    logger.warning("jarvis.db introuvable")
    return None

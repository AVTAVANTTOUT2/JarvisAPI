"""Tâches en cours — lecture directe de la table tasks SQLite.

Retourne les tâches avec status IN ('todo', 'in_progress'),
triées par priorité descendante puis due_date ascendante.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import config as cfg

logger = logging.getLogger(__name__)

PRIORITY_COLORS: dict[str, str] = {
    "high": "#ff0040",    # rouge
    "medium": "#ffb000",  # ambre
    "low": "#00ff41",     # vert
}

PRIORITY_ICONS: dict[str, str] = {
    "high": "\u25CF",     # ●
    "medium": "\u25CB",   # ○
    "low": "\u25CC",      # ◌
}


def get_active_tasks() -> list[dict[str, Any]]:
    """Retourne les tâches actives (todo + in_progress) depuis SQLite."""
    db_path = _resolve_db_path()
    if not db_path:
        return []

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """SELECT id, title, description, priority, status, due_date, category, created_at
               FROM tasks
               WHERE status IN ('todo', 'doing', 'in_progress')
               ORDER BY
                 CASE priority
                   WHEN 'high' THEN 1
                   WHEN 'medium' THEN 2
                   WHEN 'low' THEN 3
                   ELSE 4
                 END,
                 due_date ASC NULLS LAST
               LIMIT ?""",
            (cfg.MAX_TASKS,),
        )
        rows = cur.fetchall()
        conn.close()
    except sqlite3.OperationalError as exc:
        logger.warning("Table tasks indisponible: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for row in rows:
        priority = (row["priority"] or "medium").lower()
        color = PRIORITY_COLORS.get(priority, "#888888")
        icon = PRIORITY_ICONS.get(priority, "\u25CF")
        due = row["due_date"] or ""

        # Formater la date d'échéance si présente
        due_short = ""
        if due:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(str(due).replace("Z", "+00:00"))
                due_short = dt.strftime("%d/%m")
            except ValueError:
                due_short = str(due)[:10]

        results.append({
            "id": row["id"],
            "title": str(row["title"] or "").strip()[:60],
            "priority": priority,
            "priority_color": color,
            "priority_icon": icon,
            "status": row["status"],
            "due_date": due_short,
            "category": row["category"] or "",
        })

    return results


def _resolve_db_path() -> str | None:
    root = Path(__file__).resolve().parent.parent.parent
    db_full = root / "data" / "jarvis.db"
    if db_full.exists():
        return str(db_full)
    logger.warning("jarvis.db introuvable à %s", db_full)
    return None

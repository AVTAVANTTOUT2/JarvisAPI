"""Résumés emails récents — lecture SQLite de la table email_summaries.

Retourne les derniers résumés d'emails analysés par le watcher JARVIS.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import config as cfg

logger = logging.getLogger(__name__)


def get_email_summaries() -> list[dict[str, Any]]:
    """Retourne les derniers résumés d'emails depuis SQLite."""
    db_path = _resolve_db_path()
    if not db_path:
        return []

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """SELECT id, sender, subject, summary, action_needed, priority, processed_at
               FROM email_summaries
               ORDER BY processed_at DESC
               LIMIT ?""",
            (cfg.MAX_EMAILS,),
        )
        rows = cur.fetchall()
        conn.close()
    except sqlite3.OperationalError as exc:
        logger.warning("email_summaries indisponible: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for row in rows:
        subject = (row["subject"] or "Sans objet").strip()[:80]
        sender = (row["sender"] or "Inconnu").strip()[:40]
        summary = (row["summary"] or "").strip()[:100]
        action_needed = bool(row["action_needed"])
        priority = row["priority"] or "low"

        results.append({
            "id": row["id"],
            "sender": sender,
            "subject": subject,
            "summary": summary,
            "action_needed": action_needed,
            "priority": priority,
        })

    return results


def _resolve_db_path() -> str | None:
    root = Path(__file__).resolve().parent.parent.parent
    db_full = root / "data" / "jarvis.db"
    if db_full.exists():
        return str(db_full)
    logger.warning("jarvis.db introuvable")
    return None

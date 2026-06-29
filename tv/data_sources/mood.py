"""Dernier mood enregistré — lecture SQLite de la table mood_log.

Retourne le score d'humeur et le niveau d'énergie le plus récent.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import config as cfg

logger = logging.getLogger(__name__)


def get_last_mood() -> dict[str, Any]:
    """Retourne le dernier mood enregistré."""
    db_path = _resolve_db_path()
    if not db_path:
        return {"ok": False, "mood_score": 0, "energy_level": 0}

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """SELECT mood_score, energy_level, context, created_at
               FROM mood_log
               ORDER BY created_at DESC
               LIMIT 1"""
        )
        row = cur.fetchone()
        conn.close()
    except sqlite3.OperationalError as exc:
        logger.warning("mood_log indisponible: %s", exc)
        return {"ok": False, "mood_score": 0, "energy_level": 0}

    if not row:
        return {"ok": True, "mood_score": None, "energy_level": None, "context": "", "created_at": ""}

    return {
        "ok": True,
        "mood_score": row["mood_score"],
        "energy_level": row["energy_level"],
        "context": (row["context"] or "").strip()[:100],
        "created_at": row["created_at"] or "",
    }


def _resolve_db_path() -> str | None:
    root = Path(__file__).resolve().parent.parent.parent
    db_full = root / "data" / "jarvis.db"
    if db_full.exists():
        return str(db_full)
    logger.warning("jarvis.db introuvable")
    return None

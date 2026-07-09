"""Rituels du jour — citation ironique + score productivité (table daily_rituals).

Lecture SQLite READONLY, même pattern que mood.py. Le score est figé chaque
soir par le debrief ; s'il manque (avant 21:45), on retombe sur le dernier
score connu.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def get_rituals() -> dict[str, Any]:
    """Citation + score du jour (ou dernier connu)."""
    db_path = _resolve_db_path()
    if not db_path:
        return {"ok": False, "quote": None, "score": None, "label": None}

    today = datetime.now().strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT quote, roast, productivity_score, score_detail FROM daily_rituals WHERE date = ?",
            (today,),
        ).fetchone()
        last_score = conn.execute(
            """SELECT date, productivity_score, score_detail FROM daily_rituals
               WHERE productivity_score IS NOT NULL ORDER BY date DESC LIMIT 1"""
        ).fetchone()
        conn.close()
    except sqlite3.OperationalError as exc:
        logger.warning("daily_rituals indisponible: %s", exc)
        return {"ok": False, "quote": None, "score": None, "label": None}

    quote = row["quote"] if row else None
    roast = row["roast"] if row else None
    score = row["productivity_score"] if row and row["productivity_score"] is not None else (
        last_score["productivity_score"] if last_score else None
    )
    detail_raw = (row["score_detail"] if row and row["score_detail"] else
                  (last_score["score_detail"] if last_score else None))
    label = None
    if detail_raw:
        try:
            label = json.loads(detail_raw).get("label")
        except (json.JSONDecodeError, AttributeError):
            label = None

    return {
        "ok": True,
        "date": today,
        "quote": quote,
        "roast": roast,
        "score": score,
        "label": label,
        "score_date": last_score["date"] if last_score else None,
    }


def _resolve_db_path() -> str | None:
    root = Path(__file__).resolve().parent.parent.parent
    db_full = root / "data" / "jarvis.db"
    if db_full.exists():
        return str(db_full)
    logger.warning("jarvis.db introuvable")
    return None

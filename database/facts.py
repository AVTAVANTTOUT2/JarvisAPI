"""Persistance des faits durables sur l'utilisateur."""

from __future__ import annotations

from jarvis.event_bus import event_bus
from jarvis.events import FactAdded

from .core import get_db


def add_fact(
    category: str,
    content: str,
    source: str = "conversation",
    confidence: str = "medium",
) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO user_facts (category, content, source, confidence)
               VALUES (?, ?, ?, ?)""",
            (category, content, source, confidence),
        )
        fact_id = int(cursor.lastrowid)
    event_bus.emit_nowait(FactAdded(fact_id, category, content, confidence))
    return fact_id


def get_facts(category: str | None = None, current_only: bool = True) -> list[dict]:
    clauses: list[str] = []
    params: list[str] = []
    if current_only:
        clauses.append("is_current = 1")
    if category:
        clauses.append("category = ?")
        params.append(category)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM user_facts {where} ORDER BY updated_at DESC", params
        ).fetchall()
    return [dict(row) for row in rows]


def get_all_facts_summary() -> dict[str, list[dict]]:
    summary: dict[str, list[dict]] = {}
    for fact in get_facts(current_only=True):
        summary.setdefault(fact["category"], []).append(fact)
    return summary


def invalidate_fact(fact_id: int, superseded_by: int | None = None) -> None:
    with get_db() as conn:
        conn.execute(
            """UPDATE user_facts
               SET is_current = 0, superseded_by = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (superseded_by, fact_id),
        )


def search_facts(query: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM user_facts
               WHERE is_current = 1 AND content LIKE ?
               ORDER BY updated_at DESC""",
            (f"%{query}%",),
        ).fetchall()
    return [dict(row) for row in rows]

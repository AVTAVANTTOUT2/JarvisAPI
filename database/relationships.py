"""Persistance des profils, événements et insights relationnels."""

from __future__ import annotations

import json
from typing import Any

from .core import get_db


def upsert_relationship_profile(person_id: int, **kwargs: Any) -> int:
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM relationship_profiles WHERE person_id = ?", (person_id,)
        ).fetchone()
        if existing:
            if kwargs:
                assignments = ", ".join(f"{key} = ?" for key in kwargs)
                values = [*kwargs.values(), existing["id"]]
                conn.execute(
                    f"""UPDATE relationship_profiles
                        SET {assignments}, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?""",
                    values,
                )
            return int(existing["id"])
        columns = ["person_id", *kwargs.keys()]
        placeholders = ", ".join("?" for _ in columns)
        cursor = conn.execute(
            f"INSERT INTO relationship_profiles ({', '.join(columns)}) VALUES ({placeholders})",
            [person_id, *kwargs.values()],
        )
        return int(cursor.lastrowid)


def get_relationship_profile(person_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            """SELECT rp.*, p.name, p.relationship
               FROM relationship_profiles rp
               JOIN people p ON p.id = rp.person_id
               WHERE rp.person_id = ?""",
            (person_id,),
        ).fetchone()
    return dict(row) if row else None


def get_all_relationship_profiles() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT rp.*, p.name, p.relationship, p.dynamics, p.personality_notes
               FROM relationship_profiles rp
               JOIN people p ON p.id = rp.person_id
               ORDER BY rp.updated_at DESC"""
        ).fetchall()
        result = [dict(row) for row in rows]
        profiled_ids = {row["person_id"] for row in result}
        people_rows = conn.execute(
            "SELECT * FROM people ORDER BY last_mentioned DESC"
        ).fetchall()
    for person in people_rows:
        if person["id"] not in profiled_ids:
            item = dict(person)
            item["person_id"] = item["id"]
            result.append(item)
    return result


def add_relationship_event(
    person_id: int,
    event_type: str,
    summary: str,
    event_date: str | None = None,
    impact_on_user: str | None = None,
    lessons: str | None = None,
    source: str = "imessage",
) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO relationship_events
               (person_id, event_date, event_type, summary,
                impact_on_user, lessons, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                person_id,
                event_date,
                event_type,
                summary,
                impact_on_user,
                lessons,
                source,
            ),
        )
        return int(cursor.lastrowid)


def get_relationship_timeline(person_id: int, limit: int = 20) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM relationship_events
               WHERE person_id = ?
               ORDER BY COALESCE(event_date, created_at) DESC LIMIT ?""",
            (person_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def add_cross_insight(
    insight_type: str,
    content: str,
    people_involved: list | None = None,
    evidence: str | None = None,
    actionable: str | None = None,
) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO cross_insights
               (insight_type, content, people_involved, evidence, actionable)
               VALUES (?, ?, ?, ?, ?)""",
            (
                insight_type,
                content,
                json.dumps(people_involved or []),
                evidence,
                actionable,
            ),
        )
        return int(cursor.lastrowid)


def get_active_insights() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM cross_insights
               WHERE status = 'active' ORDER BY last_seen DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def increment_insight(insight_id: int) -> None:
    with get_db() as conn:
        conn.execute(
            """UPDATE cross_insights
               SET occurrences = occurrences + 1, last_seen = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (insight_id,),
        )

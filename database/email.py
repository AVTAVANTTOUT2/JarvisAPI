"""Persistance des résumés et contenus d'emails."""

from __future__ import annotations

from .core import get_db
from .time_buckets import sqlite_utc_timestamp


def upsert_email_summary(
    gmail_id: str,
    sender: str,
    subject: str,
    summary: str,
    action_needed: bool = False,
    priority: str = "medium",
) -> int:
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM email_summaries WHERE gmail_id = ?", (gmail_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE email_summaries
                   SET sender = ?, subject = ?, summary = ?,
                       action_needed = ?, priority = ?
                   WHERE gmail_id = ?""",
                (sender, subject, summary, int(action_needed), priority, gmail_id),
            )
            return int(existing["id"])
        cursor = conn.execute(
            """INSERT INTO email_summaries
               (gmail_id, sender, subject, summary, action_needed, priority)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (gmail_id, sender, subject, summary, int(action_needed), priority),
        )
        return int(cursor.lastrowid)


def get_recent_email_summaries(
    limit: int = 30, action_needed_only: bool = False
) -> list[dict]:
    with get_db() as conn:
        if action_needed_only:
            rows = conn.execute(
                """SELECT * FROM email_summaries
                   WHERE action_needed = 1
                   ORDER BY processed_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM email_summaries ORDER BY processed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def get_processed_email_ids(limit: int = 200) -> set[str]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT gmail_id FROM email_summaries ORDER BY processed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {row["gmail_id"] for row in rows if row["gmail_id"]}


def get_all_processed_email_ids() -> set[str]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT gmail_id FROM email_summaries
               WHERE gmail_id IS NOT NULL AND TRIM(gmail_id) != ''"""
        ).fetchall()
    return {str(row["gmail_id"]).strip() for row in rows if row["gmail_id"]}


def save_email_full(
    gmail_id: str,
    sender: str,
    subject: str,
    body: str,
    received_at: str,
    summary: str,
    category: str = "info",
    priority: str = "low",
    is_read: bool = False,
) -> int:
    now_iso = sqlite_utc_timestamp()
    normalized_received_at = received_at
    if received_at:
        try:
            normalized_received_at = sqlite_utc_timestamp(received_at)
        except (TypeError, ValueError):
            # Compatibilité des anciennes dates Mail localisées déjà persistées.
            normalized_received_at = received_at
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM email_summaries WHERE gmail_id = ?", (gmail_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE email_summaries SET
                       sender = ?, subject = ?, body = ?, received_at = ?,
                       summary = ?, category = ?, priority = ?, is_read = ?,
                       created_at = ?
                   WHERE gmail_id = ?""",
                (
                    sender,
                    subject,
                    body,
                    normalized_received_at,
                    summary,
                    category,
                    priority,
                    int(is_read),
                    now_iso,
                    gmail_id,
                ),
            )
            return int(existing["id"])
        cursor = conn.execute(
            """INSERT INTO email_summaries
               (gmail_id, sender, subject, body, received_at, summary,
                category, priority, is_read, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                gmail_id,
                sender,
                subject,
                body,
                normalized_received_at,
                summary,
                category,
                priority,
                int(is_read),
                now_iso,
            ),
        )
        return int(cursor.lastrowid)


def cache_email_preview(
    *,
    gmail_id: str,
    sender: str,
    subject: str,
    preview: str,
    received_at: str,
    is_read: bool,
) -> int:
    """Met à jour le cache live sans écraser un corps complet déjà analysé."""

    with get_db() as conn:
        existing = conn.execute(
            "SELECT body, summary, category, priority FROM email_summaries WHERE gmail_id = ?",
            (gmail_id,),
        ).fetchone()
    if existing:
        body = str(existing["body"] or "")
        if len(preview or "") > len(body):
            body = preview or ""
        summary = str(existing["summary"] or "") or subject
        category = str(existing["category"] or "info")
        priority = str(existing["priority"] or "low")
    else:
        body = preview or ""
        summary = subject
        category = "info"
        priority = "low"
    return save_email_full(
        gmail_id=gmail_id,
        sender=sender,
        subject=subject,
        body=body,
        received_at=received_at,
        summary=summary,
        category=category,
        priority=priority,
        is_read=is_read,
    )


def get_unread_emails_from_db(limit: int = 20) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT gmail_id, sender, subject, body, received_at,
                      summary, category, priority
               FROM email_summaries
               WHERE is_read = 0
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_recent_emails_from_db(
    limit: int = 20, category: str | None = None
) -> list[dict]:
    with get_db() as conn:
        if category:
            rows = conn.execute(
                """SELECT gmail_id, sender, subject, body, received_at,
                          summary, category, priority, is_read
                   FROM email_summaries WHERE category = ?
                   ORDER BY COALESCE(NULLIF(received_at, ''), created_at) DESC,
                            created_at DESC LIMIT ?""",
                (category, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT gmail_id, sender, subject, body, received_at,
                          summary, category, priority, is_read
                   FROM email_summaries
                   ORDER BY COALESCE(NULLIF(received_at, ''), created_at) DESC,
                            created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def mark_email_read(gmail_id: str, is_read: bool = True) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE email_summaries SET is_read = ? WHERE gmail_id = ?",
            (int(is_read), gmail_id),
        )


def get_email_stats() -> dict[str, int]:
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM email_summaries").fetchone()[0]
        unread = conn.execute(
            "SELECT COUNT(*) FROM email_summaries WHERE is_read = 0"
        ).fetchone()[0]
        urgent = conn.execute(
            """SELECT COUNT(*) FROM email_summaries
               WHERE is_read = 0 AND priority = 'high'"""
        ).fetchone()[0]
    return {"total": total, "unread": unread, "urgent": urgent}

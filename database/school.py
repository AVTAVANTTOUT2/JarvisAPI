"""Documents et données persistantes du domaine scolaire."""

from __future__ import annotations

from .core import get_db


def save_school_document(title: str, content: str, doc_type: str = "cours",
                          file_path: str = None, subject_id: int = None) -> int:
    """Enregistre un document scolaire uploadé. Retourne l'id en DB."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO school_documents (subject_id, title, content, doc_type, file_path)
               VALUES (?, ?, ?, ?, ?)""",
            (subject_id, title, content, doc_type, file_path),
        )
        return cur.lastrowid


def get_school_documents(limit: int = 50) -> list:
    """Retourne les documents scolaires (sans le BLOB embedding)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, subject_id, title, doc_type, file_path,
                      LENGTH(COALESCE(content, '')) AS content_length,
                      created_at
               FROM school_documents
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

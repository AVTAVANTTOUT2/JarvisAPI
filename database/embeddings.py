"""Persistance des embeddings de mémoire."""

from __future__ import annotations

from .core import get_db


def upsert_memory_embedding(
    source_type: str,
    source_id: int,
    text_preview: str,
    embedding: bytes,
    model: str,
) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO memory_embeddings
               (source_type, source_id, text_preview, embedding, model)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(source_type, source_id) DO UPDATE SET
                   text_preview = excluded.text_preview,
                   embedding = excluded.embedding,
                   model = excluded.model""",
            (source_type, source_id, text_preview, embedding, model),
        )
        return int(cursor.lastrowid)


def get_all_memory_embeddings(source_type: str | None = None) -> list[dict]:
    with get_db() as conn:
        if source_type:
            rows = conn.execute(
                "SELECT * FROM memory_embeddings WHERE source_type = ?",
                (source_type,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM memory_embeddings").fetchall()
    return [dict(row) for row in rows]

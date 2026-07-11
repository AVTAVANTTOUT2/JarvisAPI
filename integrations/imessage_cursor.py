"""Registre persistant et monotone des curseurs iMessage par consommateur."""

from __future__ import annotations

from database import get_db


def get_consumer_cursor(consumer: str) -> int:
    """Retourne l'offset ROWID d'un consommateur, ou zéro s'il est inconnu."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT last_apple_rowid FROM imessage_consumer_cursors WHERE consumer = ?",
            (consumer,),
        ).fetchone()
    return int(row["last_apple_rowid"] or 0) if row else 0


def initialize_consumer_cursor(consumer: str, rowid: int) -> int:
    """Crée un offset initial sans écraser un consommateur déjà connu."""
    value = max(0, int(rowid))
    with get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO imessage_consumer_cursors
               (consumer, last_apple_rowid, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)""",
            (consumer, value),
        )
        row = conn.execute(
            "SELECT last_apple_rowid FROM imessage_consumer_cursors WHERE consumer = ?",
            (consumer,),
        ).fetchone()
    return int(row["last_apple_rowid"] or 0) if row else value


def advance_consumer_cursor(consumer: str, rowid: int) -> int:
    """Avance atomiquement un offset sans jamais autoriser de retour arrière."""
    value = max(0, int(rowid))
    with get_db() as conn:
        conn.execute(
            """INSERT INTO imessage_consumer_cursors
               (consumer, last_apple_rowid, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(consumer) DO UPDATE SET
                   last_apple_rowid = MAX(last_apple_rowid, excluded.last_apple_rowid),
                   updated_at = CURRENT_TIMESTAMP""",
            (consumer, value),
        )
        row = conn.execute(
            "SELECT last_apple_rowid FROM imessage_consumer_cursors WHERE consumer = ?",
            (consumer,),
        ).fetchone()
    return int(row["last_apple_rowid"] or 0) if row else value

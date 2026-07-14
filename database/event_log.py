"""Journal SQLite immuable des événements du bus applicatif."""

from __future__ import annotations

import json

from jarvis.event_bus import JarvisEvent, event_bus

from .core import _current_db_path, get_db


@event_bus.on("*")
def _persist_event(event: JarvisEvent) -> None:
    """Persiste chaque événement au plus une fois grâce à son UUID."""
    # Ne jamais créer implicitement la base applicative : init_db() reste
    # l'unique propriétaire de son cycle de vie et crée event_log normalement.
    if not _current_db_path().exists():
        return
    with get_db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO event_log
                (event_id, event_type, version, timestamp, source, payload_json, checksum)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.event_type,
                event.version,
                event.timestamp,
                event.source,
                json.dumps(event.payload, ensure_ascii=False, sort_keys=True, default=str),
                event.checksum,
            ),
        )


def get_event_log(limit: int = 100, event_type: str | None = None) -> list[dict]:
    """Retourne les événements journalisés, du plus récent au plus ancien."""
    bounded_limit = max(1, min(int(limit), 1000))
    with get_db() as conn:
        if event_type:
            rows = conn.execute(
                """
                SELECT * FROM event_log
                WHERE event_type = ?
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (event_type, bounded_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM event_log
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()

    events: list[dict] = []
    for row in rows:
        event = dict(row)
        try:
            event["payload"] = json.loads(event.pop("payload_json"))
        except (json.JSONDecodeError, TypeError):
            event["payload"] = {}
        events.append(event)
    return events


def get_unprocessed_events(limit: int = 100) -> list[dict]:
    """Liste les événements sans marque de traitement, prêts pour un futur replay."""
    bounded_limit = max(1, min(int(limit), 1000))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM event_log
            WHERE processed_by IS NULL
            ORDER BY timestamp, id
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
    return [dict(row) for row in rows]

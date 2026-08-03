"""Journal SQLite immuable des événements du bus applicatif."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from jarvis.event_bus import JarvisEvent, event_bus

from .core import _current_db_path, get_db


@dataclass(frozen=True)
class EventReplayWindow:
    """Fenêtre durable ordonnée destinée à un consommateur SSE.

    ``resume_after`` est le curseur que le client doit considérer comme
    acquis avant le premier événement retourné. ``skipped`` n'est non nul
    que lorsque le retard dépasse la limite de reprise explicite.
    """

    events: tuple[dict[str, Any], ...]
    requested_after: int | None
    resume_after: int
    skipped: int = 0
    reset_reason: str | None = None


def _decode_payload(raw: object) -> dict[str, Any]:
    try:
        payload = json.loads(raw if isinstance(raw, str) else "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _row_to_stream_event(row: Any) -> dict[str, Any]:
    values = dict(row)
    payload = _decode_payload(values.pop("payload_json", None))
    source = str(values.get("source") or "unknown")
    event_type = str(values.get("event_type") or "unknown")
    return {
        "sse_id": int(values["id"]),
        "event_id": values["event_id"],
        "event_type": event_type,
        "type": event_type,
        "version": int(values.get("version") or 1),
        "timestamp": float(values.get("timestamp") or 0),
        "source": source,
        "payload": payload,
        "data": payload,
        "agent": None if source == "unknown" else source,
        "checksum": values.get("checksum"),
    }


@event_bus.on("*")
def _persist_event(event: JarvisEvent) -> None:
    """Persiste chaque événement au plus une fois grâce à son UUID."""
    # Ne jamais créer implicitement la base applicative : init_db() reste
    # l'unique propriétaire de son cycle de vie et crée event_log normalement.
    if not _current_db_path().exists():
        return
    with get_db() as conn:
        conversation_id = event.payload.get("conversation_id")
        if event.event_type in {"conversation.updated", "message.sent"} and isinstance(
            conversation_id, int
        ):
            exists = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if exists is None:
                return
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
                json.dumps(
                    event.payload, ensure_ascii=False, sort_keys=True, default=str
                ),
                event.checksum,
            ),
        )


def get_event_log(limit: int = 100, event_type: str | None = None) -> list[dict]:
    """Retourne la trace d'observabilité, du plus récent au plus ancien.

    Cette table porte la reprise du transport SSE par identifiant croissant,
    mais ne constitue pas un outbox de retraitement métier.
    """
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


def get_event_replay_window(
    after_id: int | None,
    *,
    initial_limit: int = 30,
    replay_limit: int = 1000,
) -> EventReplayWindow:
    """Retourne une reprise SSE croissante et bornée par l'identifiant SQLite.

    Sans curseur, seuls les derniers ``initial_limit`` événements sont
    renvoyés. Avec un curseur, tous les événements suivants sont repris tant
    que le retard reste sous ``replay_limit``. Au-delà, la fenêtre saute vers
    les plus récents et décrit explicitement le trou via ``reset_reason`` et
    ``skipped`` : aucun tampon mémoire non borné n'est créé par client.
    """
    if after_id is not None and (isinstance(after_id, bool) or after_id < 0):
        raise ValueError("after_id doit être un entier positif ou nul")
    bounded_initial = max(1, min(int(initial_limit), 200))
    bounded_replay = max(1, min(int(replay_limit), 5000))

    with get_db() as conn:
        bounds = conn.execute(
            "SELECT MIN(id) AS oldest_id, MAX(id) AS latest_id FROM event_log"
        ).fetchone()
        oldest_id = (
            int(bounds["oldest_id"]) if bounds["oldest_id"] is not None else None
        )
        latest_id = (
            int(bounds["latest_id"]) if bounds["latest_id"] is not None else None
        )

        if latest_id is None:
            return EventReplayWindow(
                events=(),
                requested_after=after_id,
                resume_after=0,
                reset_reason="cursor_ahead" if after_id not in (None, 0) else None,
            )

        if after_id is None:
            rows = conn.execute(
                "SELECT * FROM event_log ORDER BY id DESC LIMIT ?",
                (bounded_initial,),
            ).fetchall()
            events = tuple(_row_to_stream_event(row) for row in reversed(rows))
            return EventReplayWindow(
                events=events,
                requested_after=None,
                resume_after=int(events[0]["sse_id"]) - 1 if events else latest_id,
            )

        if after_id > latest_id:
            rows = conn.execute(
                "SELECT * FROM event_log ORDER BY id DESC LIMIT ?",
                (bounded_initial,),
            ).fetchall()
            events = tuple(_row_to_stream_event(row) for row in reversed(rows))
            return EventReplayWindow(
                events=events,
                requested_after=after_id,
                resume_after=int(events[0]["sse_id"]) - 1 if events else 0,
                reset_reason="cursor_ahead",
            )

        remaining = int(
            conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE id > ?", (after_id,)
            ).fetchone()[0]
        )
        reset_reason = (
            "history_truncated"
            if oldest_id is not None and after_id < oldest_id - 1
            else None
        )
        if remaining > bounded_replay:
            rows = conn.execute(
                "SELECT * FROM event_log WHERE id > ? ORDER BY id DESC LIMIT ?",
                (after_id, bounded_replay),
            ).fetchall()
            events = tuple(_row_to_stream_event(row) for row in reversed(rows))
            return EventReplayWindow(
                events=events,
                requested_after=after_id,
                resume_after=int(events[0]["sse_id"]) - 1,
                skipped=remaining - len(events),
                reset_reason="replay_limit_exceeded",
            )

        rows = conn.execute(
            "SELECT * FROM event_log WHERE id > ? ORDER BY id ASC LIMIT ?",
            (after_id, bounded_replay),
        ).fetchall()
        events = tuple(_row_to_stream_event(row) for row in rows)
        return EventReplayWindow(
            events=events,
            requested_after=after_id,
            resume_after=(
                int(events[0]["sse_id"]) - 1
                if reset_reason == "history_truncated" and events
                else after_id
            ),
            reset_reason=reset_reason,
        )

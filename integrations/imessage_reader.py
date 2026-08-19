"""Lecteur iMessage — source unique : le miroir ``jarvis.db``.

``chat.db`` n'est ouvert que par l'importeur. Ici on lit ``imessage_messages``
(reçus et envoyés), y compris quand Messages.app verrouille le fichier Apple.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from database import DB_PATH, get_db
from integrations.apple_data import apple_epoch_to_datetime
from integrations.imessage_cursor import (
    advance_consumer_cursor,
    initialize_consumer_cursor,
)


logger = logging.getLogger(__name__)

_apple_ts_to_datetime = apple_epoch_to_datetime
_apple_ts_to_datetime_from_value = apple_epoch_to_datetime

_MESSAGE_PREDICATE = "m.text IS NOT NULL AND LENGTH(TRIM(m.text)) > 0"


class IMessageReader:
    """API historique, branchée sur le miroir JARVIS plutôt que sur ``chat.db``."""

    def __init__(self, data_service: Any | None = None) -> None:
        del data_service
        self._available: bool | None = None
        self.cursor_name = "reader.intelligence"

    @property
    def db_path(self) -> Path:
        return Path(str(DB_PATH))

    @db_path.setter
    def db_path(self, value: str | Path) -> None:
        del value
        self._available = None

    def is_available(self) -> bool:
        """True si le miroir ``imessage_messages`` est lisible — pas ``chat.db``."""
        if self._available is not None:
            return self._available
        try:
            with get_db() as conn:
                conn.execute("SELECT 1 FROM imessage_messages LIMIT 1")
            self._available = True
            logger.info("[iMsgReader] miroir jarvis.db lisible")
        except Exception as exc:
            self._available = False
            logger.warning("[iMsgReader] miroir iMessage illisible : %s", exc)
        return self._available

    def count_messages(self) -> int:
        if not self.is_available():
            return 0
        try:
            with get_db() as conn:
                row = conn.execute(
                    f"SELECT COUNT(*) AS c FROM imessage_messages m WHERE {_MESSAGE_PREDICATE}"
                ).fetchone()
            return int(row["c"] if row else 0)
        except Exception as exc:
            logger.warning("[iMsgReader] count_messages : %s", exc)
            return 0

    def get_max_rowid(self) -> int:
        if not self.is_available():
            return 0
        try:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT COALESCE(MAX(apple_rowid), 0) AS m FROM imessage_messages"
                ).fetchone()
            return int(row["m"] if row else 0)
        except Exception as exc:
            logger.warning("[iMsgReader] get_max_rowid : %s", exc)
            return 0

    def get_new_messages(
        self,
        since_rowid: int,
        *,
        handle: str | None = None,
        incoming_only: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Messages ``apple_rowid > since``, ordre croissant — reçus et envoyés."""
        if not self.is_available():
            return []
        predicates = ["m.apple_rowid > ?", _MESSAGE_PREDICATE]
        parameters: list[Any] = [int(since_rowid)]
        if incoming_only:
            predicates.append("m.is_from_me = 0")
        if handle is not None:
            predicates.append("h.handle = ?")
            parameters.append(handle)
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            parameters.append(max(0, int(limit)))
        query = f"""
            SELECT m.apple_rowid AS rowid, m.text, m.date, m.is_from_me,
                   h.handle AS handle
            FROM imessage_messages m
            LEFT JOIN imessage_handles h ON h.id = m.handle_id
            WHERE {' AND '.join(predicates)}
            ORDER BY m.apple_rowid ASC{limit_sql}
        """
        try:
            with get_db() as conn:
                rows = conn.execute(query, parameters).fetchall()
        except Exception as exc:
            logger.error("[iMsgReader] get_new_messages : %s", exc)
            return []
        return [
            {
                "rowid": int(row["rowid"]),
                "text": row["text"] or "",
                "date": row["date"],
                "is_from_me": bool(row["is_from_me"]),
                "handle": row["handle"],
                "handle_id": row["handle"],
            }
            for row in rows
        ]

    def get_recent_messages(
        self,
        *,
        limit: int = 50,
        incoming_only: bool = False,
    ) -> list[dict[str, Any]]:
        if not self.is_available():
            return []
        incoming_clause = "AND m.is_from_me = 0" if incoming_only else ""
        try:
            with get_db() as conn:
                rows = conn.execute(
                    f"""
                    SELECT m.apple_rowid AS rowid, m.text, m.date, m.is_from_me,
                           h.handle AS handle
                    FROM imessage_messages m
                    LEFT JOIN imessage_handles h ON h.id = m.handle_id
                    WHERE {_MESSAGE_PREDICATE} {incoming_clause}
                    ORDER BY m.date DESC, m.apple_rowid DESC
                    LIMIT ?
                    """,
                    (max(0, int(limit)),),
                ).fetchall()
        except Exception as exc:
            logger.error("[iMsgReader] get_recent_messages : %s", exc)
            return []
        return [dict(row) for row in rows]

    def get_all_contacts(self) -> list[dict]:
        if not self.is_available():
            return []
        try:
            with get_db() as conn:
                rows = conn.execute(
                    f"""
                    SELECT h.handle AS handle, COUNT(m.id) AS msg_count,
                           MAX(m.date) AS last_date
                    FROM imessage_messages m
                    JOIN imessage_handles h ON m.handle_id = h.id
                    WHERE {_MESSAGE_PREDICATE}
                    GROUP BY h.handle
                    ORDER BY msg_count DESC
                    """
                ).fetchall()
        except Exception as exc:
            logger.error("[imessage_reader] get_all_contacts : %s", exc)
            return []
        result: list[dict] = []
        for row in rows:
            date = apple_epoch_to_datetime(row["last_date"])
            result.append(
                {
                    "handle": row["handle"],
                    "msg_count": int(row["msg_count"] or 0),
                    "last_date": date.isoformat() if date else None,
                }
            )
        return result

    def get_all_conversation_stats_full(self) -> list[dict]:
        if not self.is_available():
            return []
        try:
            with get_db() as conn:
                rows = conn.execute(
                    f"""
                    SELECT h.handle AS handle, COUNT(m.id) AS msg_count,
                           MIN(m.date) AS first_date_raw,
                           MAX(m.date) AS last_date_raw,
                           MAX(m.apple_rowid) AS last_rowid
                    FROM imessage_handles h
                    JOIN imessage_messages m ON m.handle_id = h.id
                    WHERE {_MESSAGE_PREDICATE}
                    GROUP BY h.handle
                    ORDER BY msg_count DESC
                    """
                ).fetchall()
        except Exception as exc:
            logger.error("[iMsgReader] get_all_conversation_stats_full : %s", exc)
            return []
        result: list[dict] = []
        for row in rows:
            first = apple_epoch_to_datetime(row["first_date_raw"])
            last = apple_epoch_to_datetime(row["last_date_raw"])
            result.append(
                {
                    "handle": row["handle"],
                    "msg_count": int(row["msg_count"] or 0),
                    "first_message_at": first.isoformat() if first else None,
                    "last_message_at": last.isoformat() if last else None,
                    "first_unix_ts": (
                        first.replace(tzinfo=timezone.utc).timestamp() if first else 0.0
                    ),
                    "last_unix_ts": (
                        last.replace(tzinfo=timezone.utc).timestamp() if last else 0.0
                    ),
                    "last_rowid": int(row["last_rowid"] or 0),
                }
            )
        return result

    def get_conversation(
        self,
        handle: str,
        limit: int = 100,
        since_rowid: int = 0,
    ) -> list[dict]:
        if not self.is_available() or not (handle or "").strip():
            return []
        try:
            with get_db() as conn:
                rows = conn.execute(
                    f"""
                    SELECT m.apple_rowid AS rowid, m.text, m.date, m.is_from_me,
                           h.handle AS handle
                    FROM imessage_messages m
                    JOIN imessage_handles h ON m.handle_id = h.id
                    WHERE h.handle = ? AND m.apple_rowid > ?
                      AND {_MESSAGE_PREDICATE}
                    ORDER BY m.apple_rowid ASC
                    LIMIT ?
                    """,
                    (handle, int(since_rowid), max(0, int(limit))),
                ).fetchall()
        except Exception as exc:
            logger.error("[iMsgReader] get_conversation(%s) : %s", handle, exc)
            return []
        return [self._format_conversation_row(row) for row in rows]

    def get_recent_conversation(self, handle: str, limit: int = 30) -> list[dict]:
        """N derniers messages du handle, y compris ``is_from_me = 1``."""
        if not self.is_available() or not (handle or "").strip():
            return []
        try:
            with get_db() as conn:
                rows = conn.execute(
                    f"""
                    SELECT m.apple_rowid AS rowid, m.text, m.date, m.is_from_me,
                           h.handle AS handle
                    FROM imessage_messages m
                    JOIN imessage_handles h ON m.handle_id = h.id
                    WHERE h.handle = ? AND {_MESSAGE_PREDICATE}
                    ORDER BY m.apple_rowid DESC
                    LIMIT ?
                    """,
                    (handle, max(0, int(limit))),
                ).fetchall()
        except Exception as exc:
            logger.error("[iMsgReader] get_recent_conversation(%s) : %s", handle, exc)
            return []
        return [self._format_conversation_row(row) for row in reversed(rows)]

    def get_conversation_with(self, name_or_handle: str, limit: int = 50) -> list[dict]:
        """Fil par handle / nom d'identité — jamais un LIKE sur le texte d'un autre contact."""
        if not self.is_available():
            return []
        handles = self._matching_handles(name_or_handle)
        if not handles:
            return []
        placeholders = ",".join("?" * len(handles))
        ids = [handle_id for handle_id, _ in handles]
        try:
            with get_db() as conn:
                rows = conn.execute(
                    f"""
                    SELECT m.apple_rowid AS rowid, m.text, m.date, m.is_from_me,
                           h.handle AS handle
                    FROM imessage_messages m
                    JOIN imessage_handles h ON m.handle_id = h.id
                    WHERE m.handle_id IN ({placeholders}) AND {_MESSAGE_PREDICATE}
                    ORDER BY m.apple_rowid DESC
                    LIMIT ?
                    """,
                    (*ids, max(0, int(limit))),
                ).fetchall()
        except Exception as exc:
            logger.error(
                "[iMsgReader] get_conversation_with(%s) : %s", name_or_handle, exc
            )
            return []
        return [self._format_conversation_row(row) for row in reversed(rows)]

    def get_conversation_for_period(
        self,
        handle: str,
        days: int = 90,
        limit: int = 5000,
    ) -> list[dict]:
        if not self.is_available():
            return []
        cap = min(max(limit * 4, 500), 20_000)
        raw = self.get_recent_conversation(handle, limit=cap)
        if not raw:
            return []
        cutoff = datetime.now() - timedelta(days=days)
        result: list[dict] = []
        for message in raw:
            date = apple_epoch_to_datetime(message.get("date"))
            if date is not None and date >= cutoff:
                result.append(message)
        result.sort(
            key=lambda item: apple_epoch_to_datetime(item.get("date")) or datetime.min
        )
        return result[-limit:] if len(result) > limit else result

    def search_messages(self, query: str, limit: int = 20) -> list[dict]:
        if not self.is_available() or not (query or "").strip():
            return []
        try:
            with get_db() as conn:
                rows = conn.execute(
                    f"""
                    SELECT m.apple_rowid AS rowid, m.text, m.date, m.is_from_me,
                           h.handle AS handle
                    FROM imessage_messages m
                    JOIN imessage_handles h ON m.handle_id = h.id
                    WHERE m.text LIKE ? AND {_MESSAGE_PREDICATE}
                    ORDER BY m.date DESC
                    LIMIT ?
                    """,
                    (f"%{query}%", max(0, int(limit))),
                ).fetchall()
        except Exception as exc:
            logger.error("[iMsgReader] search_messages : %s", exc)
            return []
        result: list[dict] = []
        for row in rows:
            date = apple_epoch_to_datetime(row["date"])
            result.append(
                {
                    "rowid": int(row["rowid"]),
                    "text": row["text"],
                    "date": date.isoformat() if date else None,
                    "is_from_me": bool(row["is_from_me"]),
                    "handle": row["handle"],
                }
            )
        return result

    def scan_new_messages(self) -> int:
        count, _ = self.scan_new_messages_with_last_id()
        return count

    def scan_new_messages_with_last_id(self) -> tuple[int, int]:
        if not self.is_available():
            return 0, 0
        try:
            current_max = self.get_max_rowid()
            last_max = initialize_consumer_cursor(self.cursor_name, current_max)
            if current_max <= last_max:
                return 0, current_max
            count = current_max - last_max
            advance_consumer_cursor(self.cursor_name, current_max)
            return count, current_max
        except Exception as exc:
            logger.warning("[imessage_reader] scan_new_messages_with_last_id : %s", exc)
            return 0, 0

    def peek_new_messages(
        self,
        *,
        limit: int = 100,
        incoming_only: bool = False,
        handle: str | None = None,
    ) -> tuple[list[dict], int]:
        if not self.is_available():
            return [], 0
        try:
            current_max = self.get_max_rowid()
            last_max = initialize_consumer_cursor(self.cursor_name, current_max)
            if current_max <= last_max:
                return [], last_max
            messages = self.get_new_messages(
                last_max,
                limit=limit,
                incoming_only=incoming_only,
                handle=handle,
            )
            return list(messages), last_max
        except Exception as exc:
            logger.warning("[imessage_reader] peek_new_messages : %s", exc)
            return [], 0

    def sync_knowledge_mirror(self) -> dict[str, Any]:
        """L'import appartient au worker d'ingestion, plus à ce reader."""
        if not self.is_available():
            return {"status": "unavailable"}
        return {"status": "ok", "imported": 0, "skipped": 0, "owner": "ingestion"}

    async def periodic_scan(self, interval: int = 300) -> None:
        """Intelligence sur le miroir — n'importe plus ``chat.db``."""
        logger.info(
            "[imessage_reader] Scan périodique démarré (interval=%ds)", interval
        )
        while True:
            try:
                if self.is_available():
                    messages, since_rowid = await asyncio.to_thread(
                        self.peek_new_messages, limit=100
                    )
                    if messages:
                        logger.info(
                            "[imessage_reader] %d nouveaux messages à analyser "
                            "(depuis rowid=%d)",
                            len(messages),
                            since_rowid,
                        )
                        ok = await _trigger_message_intelligence(
                            since_rowid=since_rowid,
                            messages=messages,
                        )
                        if ok:
                            max_rowid = max(int(m["rowid"]) for m in messages)
                            advance_consumer_cursor(self.cursor_name, max_rowid)
                        else:
                            logger.warning(
                                "[imessage_reader] Intelligence échouée — "
                                "curseur non avancé (retry prochain cycle)"
                            )
            except Exception as exc:
                logger.error(
                    "[imessage_reader] ÉCHEC scan — sourcing pourrait être bloqué : %s",
                    exc,
                    exc_info=True,
                )
            await asyncio.sleep(interval)

    def _matching_handles(self, name_or_handle: str) -> list[tuple[int, str]]:
        needle = (name_or_handle or "").strip()
        if not needle:
            return []
        lowered = needle.casefold()
        try:
            with get_db() as conn:
                rows = conn.execute(
                    """
                    SELECT h.id, h.handle,
                           h.display_name AS handle_display,
                           ci.display_name AS identity_display,
                           p.name AS person_name
                    FROM imessage_handles h
                    LEFT JOIN contact_identities ci ON ci.id = h.contact_identity_id
                    LEFT JOIN people p ON p.id = ci.person_id
                    """
                ).fetchall()
        except Exception as exc:
            logger.error("[iMsgReader] _matching_handles : %s", exc)
            return []
        exact: list[tuple[int, str]] = []
        fuzzy: list[tuple[int, str]] = []
        for row in rows:
            handle = str(row["handle"] or "")
            labels = [
                handle,
                str(row["handle_display"] or ""),
                str(row["identity_display"] or ""),
                str(row["person_name"] or ""),
            ]
            if any(label.casefold() == lowered for label in labels if label):
                exact.append((int(row["id"]), handle))
            elif any(lowered in label.casefold() for label in labels if label):
                fuzzy.append((int(row["id"]), handle))
        matches = exact or fuzzy
        if exact or len(matches) <= 1:
            return matches
        return self._most_recent_handle_group(matches)

    def _most_recent_handle_group(
        self, matches: list[tuple[int, str]]
    ) -> list[tuple[int, str]]:
        ids = [handle_id for handle_id, _ in matches]
        placeholders = ",".join("?" * len(ids))
        try:
            with get_db() as conn:
                row = conn.execute(
                    f"""
                    SELECT handle_id FROM imessage_messages
                    WHERE handle_id IN ({placeholders})
                    ORDER BY apple_rowid DESC LIMIT 1
                    """,
                    ids,
                ).fetchone()
        except Exception:
            return matches[:1]
        if row is None:
            return matches[:1]
        chosen = int(row["handle_id"])
        return [item for item in matches if item[0] == chosen] or matches[:1]

    @staticmethod
    def _format_conversation_row(row: Any) -> dict[str, Any]:
        date = apple_epoch_to_datetime(row["date"])
        payload = {
            "rowid": int(row["rowid"]),
            "text": row["text"],
            "date": date.isoformat() if date else None,
            "date_short": date.strftime("%d/%m %H:%M") if date else "?",
            "is_from_me": bool(row["is_from_me"]),
        }
        handle = row["handle"] if "handle" in row.keys() else None
        if handle:
            payload["handle"] = handle
        return payload


async def _trigger_message_intelligence(
    since_rowid: int,
    messages: list[dict] | None = None,
) -> bool:
    """Analyse le lot miroir sans mélanger ses ROWID avec ``messages.id``."""
    try:
        from jarvis.message_intelligence import analyze_message_batch

        batch = (
            list(messages)
            if messages is not None
            else imessage_reader.get_new_messages(since_rowid, limit=100)
        )
        result = await analyze_message_batch(
            batch,
            since_id=since_rowid,
            source="imessage",
        )
        status = result.get("status")
        if status != "ok":
            logger.debug(
                "[imessage_reader] message_intelligence terminé : %s",
                status,
            )
            return False
        return True
    except Exception as exc:
        logger.warning("[imessage_reader] message_intelligence erreur : %s", exc)
        return False


imessage_reader = IMessageReader()


__all__ = [
    "IMessageReader",
    "_apple_ts_to_datetime",
    "_apple_ts_to_datetime_from_value",
    "imessage_reader",
]

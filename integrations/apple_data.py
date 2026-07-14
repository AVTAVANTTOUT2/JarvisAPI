"""Point d'accès unique aux données Apple locales en lecture seule.

La Phase 5 centralise ici l'ouverture de ``chat.db`` et la conversion des
timestamps Apple. Les consommateurs conservent leurs contrats historiques,
mais ne construisent plus eux-mêmes de connexion SQLite vers Messages.app.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .contacts import ContactsReader, contacts_reader


DEFAULT_CHAT_DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"
APPLE_EPOCH = datetime(2001, 1, 1)
APPLE_EPOCH_OFFSET_SECONDS = 978_307_200.0
APPLE_NANOSECOND_THRESHOLD = 1e15


class _ReadOnlyConnection(sqlite3.Connection):
    """Connexion qui se ferme réellement à la sortie d'un bloc ``with``."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def apple_epoch_to_datetime(
    value: int | float | str | datetime | None,
    *,
    zero_is_none: bool = True,
) -> datetime | None:
    """Convertit une date Apple brute ou ISO en ``datetime`` naïf UTC.

    ``chat.db`` stocke selon les versions de macOS des secondes ou des
    nanosecondes depuis le 1er janvier 2001. Les chaînes ISO déjà normalisées
    sont acceptées pour préserver les contrats des lecteurs historiques.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds == 0 and zero_is_none:
        return None
    if abs(seconds) > APPLE_NANOSECOND_THRESHOLD:
        seconds /= 1_000_000_000.0
    try:
        return APPLE_EPOCH + timedelta(seconds=seconds)
    except (OverflowError, ValueError, OSError):
        return None


def datetime_to_apple_epoch(value: datetime, *, nanoseconds: bool = True) -> int:
    """Convertit un ``datetime`` en timestamp Apple secondes/nanosecondes."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    seconds = (value - APPLE_EPOCH).total_seconds()
    return int(seconds * 1_000_000_000) if nanoseconds else int(seconds)


class AppleDataService:
    """Façade de lecture de Messages.app et de résolution Contacts.app."""

    def __init__(
        self,
        db_path: str | Path = DEFAULT_CHAT_DB_PATH,
        *,
        contacts: ContactsReader = contacts_reader,
    ) -> None:
        self.db_path = Path(db_path)
        self._contacts = contacts

    def with_db_path(self, db_path: str | Path) -> AppleDataService:
        """Retourne une instance équivalente pointant vers une autre base."""
        return AppleDataService(db_path, contacts=self._contacts)

    def connect_readonly(self, *, timeout: float = 5.0) -> sqlite3.Connection:
        """Ouvre une connexion SQLite URI strictement en lecture seule."""
        connection = sqlite3.connect(
            f"file:{self.db_path}?mode=ro",
            uri=True,
            timeout=timeout,
            factory=_ReadOnlyConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    def is_available(self) -> bool:
        """Indique si la base existe et si la table ``message`` est lisible."""
        if not self.db_path.exists():
            return False
        try:
            with self.connect_readonly() as connection:
                connection.execute("SELECT COUNT(*) FROM message LIMIT 1").fetchone()
            return True
        except (sqlite3.Error, OSError):
            return False

    def health(self) -> dict[str, Any]:
        """Retourne le diagnostic minimal utilisé par les daemons et l'API."""
        result: dict[str, Any] = {
            "path": str(self.db_path),
            "exists": self.db_path.exists(),
            "readable": False,
        }
        if not result["exists"]:
            return result
        try:
            with self.connect_readonly() as connection:
                result.update(
                    readable=True,
                    message_count=int(
                        connection.execute("SELECT COUNT(*) FROM message").fetchone()[0]
                    ),
                    max_rowid=int(
                        connection.execute(
                            "SELECT COALESCE(MAX(ROWID), 0) FROM message"
                        ).fetchone()[0]
                    ),
                    handle_count=int(
                        connection.execute("SELECT COUNT(*) FROM handle").fetchone()[0]
                    ),
                )
        except (sqlite3.Error, OSError) as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    def count_messages(self) -> int:
        """Retourne le nombre total de messages accessibles."""
        with self.connect_readonly() as connection:
            row = connection.execute("SELECT COUNT(*) FROM message").fetchone()
        return int(row[0]) if row else 0

    def get_max_rowid(self) -> int:
        """Retourne le plus grand ROWID de la table ``message``."""
        with self.connect_readonly() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(ROWID), 0) FROM message"
            ).fetchone()
        return int(row[0]) if row else 0

    def get_new_messages(
        self,
        since_rowid: int,
        *,
        handle: str | None = None,
        incoming_only: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Retourne les messages postérieurs à ``since_rowid`` par ROWID."""
        predicates = [
            "m.ROWID > ?",
            "m.text IS NOT NULL",
            "m.text != ''",
        ]
        parameters: list[Any] = [int(since_rowid)]
        if incoming_only:
            predicates.append("m.is_from_me = 0")
        if handle is not None:
            predicates.append("h.id = ?")
            parameters.append(handle)
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            parameters.append(max(0, int(limit)))
        query = f"""
            SELECT m.ROWID AS rowid, m.text, m.date, m.is_from_me,
                   h.id AS handle
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            WHERE {' AND '.join(predicates)}
            ORDER BY m.ROWID ASC{limit_sql}
        """
        with self.connect_readonly() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {
                "rowid": int(row["rowid"]),
                "text": row["text"] or "",
                "date": row["date"],
                "is_from_me": bool(row["is_from_me"]),
                "handle": row["handle"],
            }
            for row in rows
        ]

    def get_recent_messages(
        self,
        *,
        limit: int = 50,
        incoming_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Retourne les messages récents, du plus récent au plus ancien."""
        incoming_clause = "AND m.is_from_me = 0" if incoming_only else ""
        with self.connect_readonly() as connection:
            rows = connection.execute(
                f"""
                SELECT m.ROWID AS rowid, m.text, m.date, m.is_from_me,
                       h.id AS handle
                FROM message m
                LEFT JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.text IS NOT NULL AND m.text != '' {incoming_clause}
                ORDER BY m.date DESC
                LIMIT ?
                """,
                (max(0, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_contacts(self) -> list[dict[str, Any]]:
        """Retourne les handles iMessage uniques avec activité agrégée."""
        with self.connect_readonly() as connection:
            rows = connection.execute(
                """
                SELECT h.id AS handle, COUNT(m.ROWID) AS msg_count,
                       MAX(m.date) AS last_date
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.text IS NOT NULL AND LENGTH(TRIM(m.text)) > 0
                GROUP BY h.id
                ORDER BY msg_count DESC
                """
            ).fetchall()
        result: list[dict[str, Any]] = []
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

    def get_all_conversation_stats(self) -> list[dict[str, Any]]:
        """Retourne les statistiques de toutes les conversations iMessage."""
        with self.connect_readonly() as connection:
            rows = connection.execute(
                """
                SELECT h.id AS handle, COUNT(m.ROWID) AS msg_count,
                       MIN(m.date) AS first_date_raw,
                       MAX(m.date) AS last_date_raw,
                       MAX(m.ROWID) AS last_rowid
                FROM chat c
                JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
                JOIN handle h ON h.ROWID = chj.handle_id
                JOIN message m ON m.handle_id = h.ROWID
                WHERE h.id IS NOT NULL
                  AND m.text IS NOT NULL
                  AND LENGTH(TRIM(m.text)) > 0
                GROUP BY h.id
                ORDER BY msg_count DESC
                """
            ).fetchall()
        result: list[dict[str, Any]] = []
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
        *,
        limit: int = 100,
        since_rowid: int = 0,
    ) -> list[dict[str, Any]]:
        """Retourne une conversation en ordre chronologique depuis un ROWID."""
        with self.connect_readonly() as connection:
            rows = connection.execute(
                """
                SELECT m.ROWID AS rowid, m.text, m.date, m.is_from_me
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                WHERE h.id = ? AND m.ROWID > ?
                  AND m.text IS NOT NULL AND LENGTH(m.text) > 0
                ORDER BY m.ROWID ASC
                LIMIT ?
                """,
                (handle, int(since_rowid), max(0, int(limit))),
            ).fetchall()
        return [self._format_conversation_row(row) for row in rows]

    def get_recent_conversation(
        self,
        handle: str,
        *,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Retourne la fin d'une conversation en ordre chronologique."""
        with self.connect_readonly() as connection:
            rows = connection.execute(
                """
                SELECT m.ROWID AS rowid, m.text, m.date, m.is_from_me
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                WHERE h.id = ? AND m.text IS NOT NULL AND LENGTH(m.text) > 0
                ORDER BY m.ROWID DESC
                LIMIT ?
                """,
                (handle, max(0, int(limit))),
            ).fetchall()
        return [self._format_conversation_row(row) for row in reversed(rows)]

    def get_conversation_with(
        self,
        name_or_handle: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Résout un handle par motif puis retourne ses messages récents."""
        pattern = f"%{name_or_handle}%"
        with self.connect_readonly() as connection:
            row = connection.execute(
                """
                SELECT DISTINCT h.id
                FROM handle h
                LEFT JOIN message m ON m.handle_id = h.ROWID
                WHERE h.id LIKE ? OR (m.text IS NOT NULL AND m.text LIKE ?)
                ORDER BY h.id
                LIMIT 1
                """,
                (pattern, pattern),
            ).fetchone()
        if not row:
            return []
        handle = str(row["id"])
        messages = self.get_recent_conversation(handle, limit=limit)
        for message in messages:
            message["handle"] = handle
        return messages

    def search_messages(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Recherche un texte dans tous les messages iMessage."""
        with self.connect_readonly() as connection:
            rows = connection.execute(
                """
                SELECT m.ROWID AS rowid, m.text, m.date, m.is_from_me,
                       h.id AS handle
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.text LIKE ? AND m.text IS NOT NULL
                ORDER BY m.date DESC
                LIMIT ?
                """,
                (f"%{query}%", max(0, int(limit))),
            ).fetchall()
        result: list[dict[str, Any]] = []
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

    def resolve_handle(self, handle: str) -> str:
        """Résout un numéro ou email avec le cache Contacts.app."""
        return self._contacts.resolve_handle(handle)

    @staticmethod
    def _format_conversation_row(row: sqlite3.Row) -> dict[str, Any]:
        date = apple_epoch_to_datetime(row["date"])
        return {
            "rowid": int(row["rowid"]),
            "text": row["text"],
            "date": date.isoformat() if date else None,
            "date_short": date.strftime("%d/%m %H:%M") if date else "?",
            "is_from_me": bool(row["is_from_me"]),
        }


apple_data = AppleDataService()


__all__ = [
    "APPLE_EPOCH",
    "APPLE_EPOCH_OFFSET_SECONDS",
    "APPLE_NANOSECOND_THRESHOLD",
    "DEFAULT_CHAT_DB_PATH",
    "AppleDataService",
    "apple_data",
    "apple_epoch_to_datetime",
    "datetime_to_apple_epoch",
]

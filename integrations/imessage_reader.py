"""Lecteur iMessage — accès READONLY à ~/Library/Messages/chat.db.

Utilisé par le RelationshipAnalyzer pour extraire l'historique des conversations.
Distinct du bridge iMessage (integrations/imessage.py) qui gère le polling temps réel + envoi.
"""

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .imessage_cursor import (
    advance_consumer_cursor,
    initialize_consumer_cursor,
)

logger = logging.getLogger(__name__)

# Les dates iMessage sont des secondes depuis 2001-01-01 00:00:00 UTC,
# MAIS Apple les stocke aussi en nanosecondes dans les versions récentes de macOS.
APPLE_EPOCH = datetime(2001, 1, 1)


def _apple_ts_to_datetime_from_value(val) -> datetime | None:
    """Interprète une date renvoyée par le reader : chaîne ISO ou timestamp Apple brut."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    try:
        return _apple_ts_to_datetime(float(val))
    except (TypeError, ValueError):
        return None


def _apple_ts_to_datetime(ts: int | float | None) -> datetime | None:
    """Convertit un timestamp Apple (chat.db) en datetime Python."""
    if ts is None or ts == 0:
        return None
    # macOS récent : nanosecondes (valeur > 1e15)
    if abs(ts) > 1e15:
        ts = ts / 1e9
    try:
        return APPLE_EPOCH + timedelta(seconds=ts)
    except (OverflowError, ValueError, OSError):
        return None


class IMessageReader:
    """Accès READONLY à chat.db pour l'analyse relationnelle."""

    def __init__(self):
        self.db_path = Path.home() / "Library" / "Messages" / "chat.db"
        self._available: bool | None = None
        self.cursor_name = "reader.intelligence"

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        logger.info("[imessage_reader] Tentative accès chat.db : %s", self.db_path)
        logger.info("[imessage_reader] Fichier existe : %s", self.db_path.exists())
        try:
            if not self.db_path.exists():
                self._available = False
                logger.warning("[imessage_reader] chat.db introuvable — chemin : %s", self.db_path)
                return False
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            conn.execute("SELECT COUNT(*) FROM message LIMIT 1")
            conn.close()
            self._available = True
            logger.info("[iMsgReader] chat.db accessible en lecture")
        except sqlite3.OperationalError as e:
            self._available = False
            err = str(e).lower()
            logger.warning(
                "[iMsgReader] chat.db inaccessible (OperationalError) : %s — %s",
                e,
                "Full Disk Access requis pour l’app qui lance JARVIS (Terminal / Cursor)."
                if "unable to open" in err or "permission" in err or "authorization" in err
                else "",
            )
        except Exception as e:
            logger.warning("[iMsgReader] chat.db inaccessible : %s", e)
            self._available = False
        return self._available

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def get_all_contacts(self) -> list[dict]:
        """Contacts uniques avec nombre de messages et dernière date."""
        if not self.is_available():
            return []
        try:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT h.id AS handle,
                       COUNT(m.ROWID) AS msg_count,
                       MAX(m.date) AS last_date
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.text IS NOT NULL AND LENGTH(TRIM(m.text)) > 0
                GROUP BY h.id
                ORDER BY msg_count DESC
                """
            ).fetchall()
            conn.close()
            result: list[dict] = []
            for r in rows:
                dt = _apple_ts_to_datetime(r["last_date"])
                result.append(
                    {
                        "handle": r["handle"],
                        "msg_count": r["msg_count"],
                        "last_date": dt.isoformat() if dt else None,
                    }
                )
            return result
        except Exception as e:
            logger.error("[imessage_reader] get_all_contacts : %s", e)
            return []

    def get_all_conversation_stats_full(self) -> list[dict]:
        """Retourne TOUTES les conversations distinctes avec stats et dates corrigées.

        Joint `chat`, `chat_handle_join`, `handle`, `message`.
        Conversion date Cocoa/macOS:
          - nanosecondes: (date / 1e9) + 978307200
          - secondes: date + 978307200
        """
        if not self.is_available():
            return []
        try:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT
                    h.id AS handle,
                    COUNT(m.ROWID) AS msg_count,
                    MIN(m.date) AS first_date_raw,
                    MAX(m.date) AS last_date_raw,
                    MAX(m.ROWID) AS last_rowid,
                    MIN(
                        CASE
                            WHEN ABS(m.date) > 1000000000000
                                THEN (m.date / 1000000000.0) + 978307200
                            ELSE m.date + 978307200
                        END
                    ) AS first_unix_ts,
                    MAX(
                        CASE
                            WHEN ABS(m.date) > 1000000000000
                                THEN (m.date / 1000000000.0) + 978307200
                            ELSE m.date + 978307200
                        END
                    ) AS last_unix_ts
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
            conn.close()
            out: list[dict] = []
            for r in rows:
                first_dt = _apple_ts_to_datetime(r["first_date_raw"])
                last_dt = _apple_ts_to_datetime(r["last_date_raw"])
                out.append(
                    {
                        "handle": r["handle"],
                        "msg_count": int(r["msg_count"] or 0),
                        "first_message_at": first_dt.isoformat() if first_dt else None,
                        "last_message_at": last_dt.isoformat() if last_dt else None,
                        "first_unix_ts": float(r["first_unix_ts"] or 0),
                        "last_unix_ts": float(r["last_unix_ts"] or 0),
                        "last_rowid": int(r["last_rowid"] or 0),
                    }
                )
            return out
        except Exception as e:
            logger.error("[iMsgReader] get_all_conversation_stats_full : %s", e)
            return []

    def get_conversation(self, handle: str, limit: int = 100,
                         since_rowid: int = 0) -> list[dict]:
        """Messages d'un contact depuis un ROWID donné (analyse incrémentale)."""
        if not self.is_available():
            return []
        try:
            conn = self._connect()
            rows = conn.execute("""
                SELECT m.ROWID AS rowid, m.text, m.date, m.is_from_me
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                WHERE h.id = ?
                  AND m.ROWID > ?
                  AND m.text IS NOT NULL
                  AND LENGTH(m.text) > 0
                ORDER BY m.ROWID ASC
                LIMIT ?
            """, (handle, since_rowid, limit)).fetchall()
            conn.close()
            result = []
            for r in rows:
                dt = _apple_ts_to_datetime(r["date"])
                result.append({
                    "rowid": r["rowid"],
                    "text": r["text"],
                    "date": dt.isoformat() if dt else None,
                    "date_short": dt.strftime("%d/%m %H:%M") if dt else "?",
                    "is_from_me": bool(r["is_from_me"]),
                })
            return result
        except Exception as e:
            logger.error("[iMsgReader] get_conversation(%s) : %s", handle, e)
            return []

    def get_recent_conversation(self, handle: str, limit: int = 30) -> list[dict]:
        """Derniers messages avec ce handle (ordre chronologique, du plus ancien au plus récent)."""
        if not self.is_available():
            return []
        try:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT m.ROWID AS rowid, m.text, m.date, m.is_from_me
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                WHERE h.id = ?
                  AND m.text IS NOT NULL
                  AND LENGTH(m.text) > 0
                ORDER BY m.ROWID DESC
                LIMIT ?
                """,
                (handle, limit),
            ).fetchall()
            conn.close()
            rows = list(reversed(rows))
            result = []
            for r in rows:
                dt = _apple_ts_to_datetime(r["date"])
                result.append({
                    "rowid": r["rowid"],
                    "text": r["text"],
                    "date": dt.isoformat() if dt else None,
                    "date_short": dt.strftime("%d/%m %H:%M") if dt else "?",
                    "is_from_me": bool(r["is_from_me"]),
                })
            return result
        except Exception as e:
            logger.error("[iMsgReader] get_recent_conversation(%s) : %s", handle, e)
            return []

    def get_conversation_with(self, name_or_handle: str, limit: int = 50) -> list[dict]:
        """Cherche un handle par motif (id handle ou texte message), puis renvoie le fil."""
        if not self.is_available():
            return []
        try:
            conn = self._connect()
            q = f"%{name_or_handle}%"
            handles = conn.execute(
                """
                SELECT DISTINCT h.id
                FROM handle h
                LEFT JOIN message m ON m.handle_id = h.ROWID
                WHERE h.id LIKE ? OR (m.text IS NOT NULL AND m.text LIKE ?)
                ORDER BY h.id
                LIMIT 20
                """,
                (q, q),
            ).fetchall()
            if not handles:
                conn.close()
                return []
            handle = handles[0]["id"]
            conn.close()
            out = self.get_recent_conversation(handle, limit=limit)
            for m in out:
                m["handle"] = handle
            return out
        except Exception as e:
            logger.error("[iMsgReader] get_conversation_with(%s) : %s", name_or_handle, e)
            return []

    def get_conversation_for_period(
        self, handle: str, days: int = 90, limit: int = 5000
    ) -> list[dict]:
        """Messages dont la date est >= (maintenant − days), ordre chronologique croissant.

        Utilisé par ContactAnalytics (pas de LLM). S’appuie sur `get_recent_conversation`
        puis filtre par date pour éviter les incohérences de timestamp SQLite Apple.
        """
        if not self.is_available():
            return []
        cap = min(max(limit * 4, 500), 20000)
        raw = self.get_recent_conversation(handle, limit=cap)
        if not raw:
            return []
        cutoff = datetime.now() - timedelta(days=days)
        out: list[dict] = []
        for r in raw:
            dt = _apple_ts_to_datetime_from_value(r.get("date"))
            if dt is None:
                continue
            if dt >= cutoff:
                out.append(r)
        out.sort(key=lambda x: _apple_ts_to_datetime_from_value(x.get("date")) or datetime.min)
        if len(out) > limit:
            out = out[-limit:]
        return out

    def search_messages(self, query: str, limit: int = 20) -> list[dict]:
        """Recherche LIKE dans tous les messages."""
        if not self.is_available():
            return []
        try:
            conn = self._connect()
            rows = conn.execute("""
                SELECT m.ROWID AS rowid, m.text, m.date, m.is_from_me,
                       h.id AS handle
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.text LIKE ?
                  AND m.text IS NOT NULL
                ORDER BY m.date DESC
                LIMIT ?
            """, (f"%{query}%", limit)).fetchall()
            conn.close()
            result = []
            for r in rows:
                dt = _apple_ts_to_datetime(r["date"])
                result.append({
                    "rowid": r["rowid"],
                    "text": r["text"],
                    "date": dt.isoformat() if dt else None,
                    "is_from_me": bool(r["is_from_me"]),
                    "handle": r["handle"],
                })
            return result
        except Exception as e:
            logger.error("[iMsgReader] search_messages : %s", e)
            return []

    # ── Sourcing : scan périodique en lecture seule ──────────

    def scan_new_messages(self) -> int:
        """Lit chat.db en `mode=ro`, retourne le nombre de nouveaux messages détectés.

        Cette méthode est purement en lecture seule. Elle ne modifie jamais
        chat.db et n'appelle jamais osascript / Messages.app en écriture.
        """
        count, _ = self.scan_new_messages_with_last_id()
        return count

    def scan_new_messages_with_last_id(self) -> tuple[int, int]:
        """Comme scan_new_messages() mais retourne (count, last_rowid).

        Returns:
            Tuple (nombre_de_nouveaux_messages, dernier_rowid_scanné).
            (0, 0) si aucun nouveau message ou erreur.
        """
        if not self.is_available():
            return 0, 0
        try:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro", uri=True, timeout=5.0
            )
            row = conn.execute(
                "SELECT COALESCE(MAX(ROWID), 0) FROM message"
            ).fetchone()
            current_max = int(row[0]) if row else 0
            conn.close()

            # Au premier scan, initialise au maximum courant pour ne pas
            # retraiter tout l'historique. Aux scans suivants, récupère
            # l'offset persistant existant, y compris s'il vaut réellement 0.
            last_max = initialize_consumer_cursor(self.cursor_name, current_max)
            if current_max <= last_max:
                return 0, current_max

            count = current_max - last_max
            advance_consumer_cursor(self.cursor_name, current_max)
            return count, current_max
        except sqlite3.Error as e:
            logger.warning("[imessage_reader] scan_new_messages_with_last_id : %s", e)
            return 0, 0

    async def periodic_scan(self, interval: int = 300) -> None:
        """Boucle de lecture chat.db en lecture seule — jamais d'écriture côté Messages.app.

        Args:
            interval: secondes entre chaque scan (défaut 300 = 5 minutes)
        """
        logger.info("[imessage_reader] Scan périodique démarré (interval=%ds)", interval)
        while True:
            try:
                if self.is_available():
                    count, last_id = self.scan_new_messages_with_last_id()
                    if count:
                        logger.info(
                            "[imessage_reader] %d nouveaux messages sourcés "
                            "(jusqu'à rowid=%d)",
                            count,
                            last_id,
                        )
                        # Déclenche l'analyse en tâche séparée (jamais bloquant)
                        asyncio.create_task(
                            _trigger_message_intelligence(
                                since_id=last_id - count,
                            ),
                            name="message_intelligence",
                        )
            except Exception as e:
                logger.error(
                    "[imessage_reader] ÉCHEC scan — sourcing pourrait être "
                    "bloqué : %s",
                    e,
                    exc_info=True,
                )
            await asyncio.sleep(interval)



async def _trigger_message_intelligence(since_id: int) -> None:
    """Déclenche l'analyse d'intelligence sur les messages récents.

    Appelée en `asyncio.create_task` (non bloquant) après chaque scan
    réussi de chat.db. L'import est lazy pour éviter les cycles.
    """
    try:
        from jarvis.message_intelligence import analyze_recent_messages

        result = await analyze_recent_messages(since_id=since_id)
        if result.get("status") != "ok":
            logger.debug(
                "[imessage_reader] message_intelligence terminé : %s",
                result.get("status"),
            )
    except Exception as e:
        logger.warning(
            "[imessage_reader] message_intelligence erreur : %s", e
        )


imessage_reader = IMessageReader()

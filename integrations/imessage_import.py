"""Moteur d'import iMessage — importe chat.db dans jarvis.db.

Import idempotent, incremental, avec triple cle de deduplication :
  1. apple_rowid (message.ROWID de chat.db)
  2. guid (identifiant Apple du message)
  3. content_hash (SHA256 de date+handle+text+guid)

Architecture :
  - Import initial : lit tout chat.db par batches, importe handles → chats → messages → attachments → reactions
  - Sync incrementale : repart du curseur imessage_sync_cursor, ne lit que ROWID > last_apple_rowid
  - Reconciliation : audit post-import, detection/correction des incohérences
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config
from database import get_db

logger = logging.getLogger(__name__)

CHAT_DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"
APPLE_EPOCH = datetime(2001, 1, 1)

# Constantes de taille de batch et retry
DEFAULT_BATCH_SIZE = getattr(config, "IIMPORT_BATCH_SIZE", 5000)
DEFAULT_MAX_RETRIES = getattr(config, "IIMPORT_MAX_RETRIES", 3)

# Mapping des types de reactions iMessage (tapbacks)
REACTION_TYPE_NAMES: dict[int, str] = {
    2000: "liked",
    2001: "loved",
    2002: "disliked",
    2003: "laughed",
    2004: "emphasized",
    2005: "questioned",
}


@dataclass
class ImportResult:
    """Resultat d'un import (initial ou incremental)."""
    mode: str = "initial"            # "initial" ou "incremental"
    total_handles: int = 0
    total_chats: int = 0
    total_messages: int = 0
    total_attachments: int = 0
    total_reactions: int = 0
    total_skipped: int = 0
    total_failed: int = 0
    errors: list[str] = field(default_factory=list)
    reconciliation: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0
    completed_at: str = ""


@dataclass
class ReconciliationReport:
    """Rapport de reconciliation post-import."""
    chat_db_messages: int = 0
    jarvis_db_messages: int = 0
    chat_db_chats: int = 0
    jarvis_db_chats: int = 0
    chat_db_handles: int = 0
    jarvis_db_handles: int = 0
    orphan_messages: int = 0
    orphan_fixed: int = 0
    duplicates_found: int = 0
    duplicates_removed: int = 0
    ok: bool = False


def _apple_ts_to_iso(ts: int | float | None) -> str | None:
    """Convertit un timestamp Apple (secondes depuis 2001-01-01) en ISO 8601."""
    if ts is None:
        return None
    try:
        if abs(ts) > 1e15:
            ts = ts / 1e9
        return (APPLE_EPOCH + timedelta(seconds=ts)).isoformat()
    except (OverflowError, ValueError, OSError):
        return None


def _compute_content_hash(
    date_raw: int | None,
    handle_id: int | None,
    text: str | None,
    guid: str | None,
) -> str:
    """Calcule un hash SHA256 unique pour un message.

    Combine date brute (timestamp Apple), handle_id (ROWID du handle dans chat.db),
    texte + guid pour produire une empreinte stable.
    """
    components = [
        str(date_raw or 0),
        str(handle_id or 0),
        (text or "").strip(),
        (guid or "").strip(),
    ]
    combined = "||".join(components).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()


class IMessageImporter:
    """Importe les donnees iMessage de chat.db vers jarvis.db.

    Usage :
        importer = IMessageImporter()
        result = importer.import_all()           # import initial
        result = importer.sync_incremental()     # sync incrementale
        report = importer.reconcile()            # audit post-import
        importer.reset_cursor()                  # reinitialiser le curseur
    """

    def __init__(self, batch_size: int = DEFAULT_BATCH_SIZE):
        self.batch_size = batch_size
        self._chat_db_conn: sqlite3.Connection | None = None
        self._available: bool | None = None

    # ── Disponibilite ──────────────────────────────────────────

    def is_available(self) -> bool:
        """Verifie l'acces a chat.db en lecture seule."""
        if self._available is not None:
            return self._available
        if not CHAT_DB_PATH.exists():
            logger.warning("[imessage_import] chat.db introuvable : %s", CHAT_DB_PATH)
            self._available = False
            return False
        try:
            conn = sqlite3.connect(f"file:{CHAT_DB_PATH}?mode=ro", uri=True, timeout=5.0)
            conn.execute("SELECT COUNT(*) FROM message LIMIT 1")
            conn.close()
            self._available = True
            logger.info("[imessage_import] chat.db accessible en lecture")
        except sqlite3.OperationalError as e:
            logger.warning(
                "[imessage_import] chat.db inaccessible : %s — "
                "Full Disk Access requis pour l'app qui lance JARVIS.",
                e,
            )
            self._available = False
        return self._available

    def _open_chat_db(self) -> sqlite3.Connection:
        """Ouvre chat.db en lecture seule."""
        conn = sqlite3.connect(f"file:{CHAT_DB_PATH}?mode=ro", uri=True, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _close_chat_db(self) -> None:
        if self._chat_db_conn:
            try:
                self._chat_db_conn.close()
            except sqlite3.Error:
                pass
            self._chat_db_conn = None

    # ── Curseur ────────────────────────────────────────────────

    def _get_cursor(self) -> dict[str, Any]:
        """Lit le curseur de synchronisation actuel."""
        with get_db() as conn:
            row = conn.execute(
                "SELECT last_apple_rowid, last_date, last_guid, total_imported, "
                "total_failed, status FROM imessage_sync_cursor WHERE id = 1"
            ).fetchone()
            if row:
                return dict(row)
        return {
            "last_apple_rowid": 0,
            "last_date": 0,
            "last_guid": "",
            "total_imported": 0,
            "total_failed": 0,
            "status": "idle",
        }

    def _update_cursor(
        self,
        last_rowid: int,
        last_date: int = 0,
        last_guid: str = "",
        total_imported: int = 0,
        total_failed: int = 0,
        status: str = "idle",
        error_message: str = "",
    ) -> None:
        """Met a jour le curseur de synchronisation."""
        with get_db() as conn:
            cur = conn.execute("SELECT id FROM imessage_sync_cursor WHERE id = 1").fetchone()
            if cur:
                conn.execute(
                    """UPDATE imessage_sync_cursor
                       SET last_apple_rowid = ?,
                           last_date = ?,
                           last_guid = ?,
                           total_imported = COALESCE(?, total_imported),
                           total_failed = COALESCE(?, total_failed),
                           last_sync_at = CURRENT_TIMESTAMP,
                           completed_at = CASE WHEN ? = 'idle' THEN CURRENT_TIMESTAMP ELSE completed_at END,
                           status = ?,
                           error_message = ?
                       WHERE id = 1""",
                    (last_rowid, last_date, last_guid,
                     total_imported, total_failed,
                     status, status, error_message),
                )
            else:
                conn.execute(
                    """INSERT INTO imessage_sync_cursor
                       (id, last_apple_rowid, last_date, last_guid, total_imported,
                        total_failed, started_at, status, error_message)
                       VALUES (1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)""",
                    (last_rowid, last_date, last_guid,
                     total_imported, total_failed, status, error_message),
                )

    def reset_cursor(self) -> None:
        """Reinitialise le curseur pour un reimport complet."""
        with get_db() as conn:
            conn.execute("DELETE FROM imessage_sync_cursor WHERE id = 1")
        logger.info("[imessage_import] Curseur reinitialise")

    def get_status(self) -> dict[str, Any]:
        """Retourne l'etat actuel du curseur + stats rapides."""
        cursor = self._get_cursor()
        with get_db() as conn:
            msg_count = conn.execute(
                "SELECT COUNT(*) c FROM imessage_messages"
            ).fetchone()["c"]
            chat_count = conn.execute(
                "SELECT COUNT(*) c FROM imessage_chats"
            ).fetchone()["c"]
            handle_count = conn.execute(
                "SELECT COUNT(*) c FROM imessage_handles"
            ).fetchone()["c"]
        return {
            **cursor,
            "jarvis_db_messages": msg_count,
            "jarvis_db_chats": chat_count,
            "jarvis_db_handles": handle_count,
        }

    # ── Import initial ─────────────────────────────────────────

    def import_all(self) -> ImportResult:
        """Import complet de chat.db → jarvis.db."""
        if not self.is_available():
            raise RuntimeError("chat.db inaccessible — verifier Full Disk Access")

        t0 = datetime.now()
        result = ImportResult(mode="initial")

        cursor = self._get_cursor()
        if cursor["status"] == "importing":
            logger.warning(
                "[imessage_import] Un import semble deja en cours "
                "(status=importing). Reprise forcee."
            )

        logger.info("[imessage_import] Demarrage import complet...")
        self._update_cursor(
            last_rowid=cursor["last_apple_rowid"],
            last_date=cursor["last_date"],
            last_guid=cursor["last_guid"],
            status="importing",
        )

        try:
            chat_conn = self._open_chat_db()

            # 1. Handles
            logger.info("[imessage_import] Phase 1/5 : handles...")
            handles_map = self._import_handles(chat_conn)
            result.total_handles = len(handles_map)
            logger.info("[imessage_import] %d handles importes", result.total_handles)

            # 2. Chats
            logger.info("[imessage_import] Phase 2/5 : chats...")
            chats_map = self._import_chats(chat_conn)
            result.total_chats = len(chats_map)
            logger.info("[imessage_import] %d chats importes", result.total_chats)

            # 3. Chat-handles (lien N-N)
            logger.info("[imessage_import] Phase 2b/5 : chat_handles...")
            self._import_chat_handles(chat_conn, handles_map, chats_map)

            # 4. Messages (par batches)
            logger.info("[imessage_import] Phase 3/5 : messages...")
            msg_result = self._import_all_messages(chat_conn, handles_map, chats_map)
            result.total_messages = msg_result["imported"]
            result.total_skipped = msg_result["skipped"]
            result.total_failed = msg_result["failed"]
            result.errors = msg_result.get("errors", [])
            logger.info(
                "[imessage_import] Messages : %d importes, %d skippes, %d echoues",
                result.total_messages, result.total_skipped, result.total_failed,
            )

            # 5. Attachments
            logger.info("[imessage_import] Phase 4/5 : attachments...")
            att_result = self._import_attachments(chat_conn)
            result.total_attachments = att_result["imported"]
            logger.info("[imessage_import] %d attachments importes", result.total_attachments)

            # 6. Reactions
            logger.info("[imessage_import] Phase 5/5 : reactions...")
            reac_result = self._import_reactions(chat_conn, handles_map)
            result.total_reactions = reac_result["imported"]
            logger.info("[imessage_import] %d reactions importees", result.total_reactions)

            self._close_chat_db()

            # Curseur final
            max_rowid = self._get_max_chat_rowid() if self.is_available() else 0
            self._update_cursor(
                last_rowid=max_rowid,
                total_imported=result.total_messages,
                total_failed=result.total_failed,
                status="idle",
                error_message="",
            )

            # Reconciliation automatique
            logger.info("[imessage_import] Reconciliation post-import...")
            result.reconciliation = self.reconcile().__dict__

        except Exception as e:
            logger.exception("[imessage_import] Echec import complet")
            self._update_cursor(
                last_rowid=cursor["last_apple_rowid"],
                status="error",
                error_message=f"{type(e).__name__}: {e}",
            )
            result.errors.append(f"Erreur fatale : {type(e).__name__}: {e}")
        finally:
            self._close_chat_db()

        result.duration_seconds = (datetime.now() - t0).total_seconds()
        result.completed_at = datetime.now(timezone.utc).isoformat()
        logger.info(
            "[imessage_import] Import termine en %.1fs — "
            "%d msg / %d skip / %d erreurs",
            result.duration_seconds,
            result.total_messages,
            result.total_skipped,
            result.total_failed,
        )
        return result

    # ── Sync incrementale ──────────────────────────────────────

    def sync_incremental(self) -> ImportResult:
        """Sync incrementale : uniquement les nouveaux messages depuis le curseur."""
        if not self.is_available():
            raise RuntimeError("chat.db inaccessible — verifier Full Disk Access")

        t0 = datetime.now()
        result = ImportResult(mode="incremental")

        cursor = self._get_cursor()
        last_rowid = cursor["last_apple_rowid"]

        chat_max_rowid = self._get_max_chat_rowid()
        if chat_max_rowid <= last_rowid:
            logger.info("[imessage_import] Aucun nouveau message (cursor=%d, max=%d)", last_rowid, chat_max_rowid)
            result.duration_seconds = (datetime.now() - t0).total_seconds()
            result.completed_at = datetime.now(timezone.utc).isoformat()
            result.reconciliation = self.reconcile().__dict__
            return result

        logger.info(
            "[imessage_import] Sync incrementale : ROWID %d → %d (%d nouveaux)",
            last_rowid, chat_max_rowid, chat_max_rowid - last_rowid,
        )

        self._update_cursor(
            last_rowid=last_rowid,
            last_date=cursor["last_date"],
            last_guid=cursor["last_guid"],
            status="importing",
        )

        try:
            chat_conn = self._open_chat_db()

            # 1. Nouveaux handles (apparus depuis le dernier import)
            handles_map = self._import_new_handles(chat_conn, cursor["last_date"])

            # 2. Nouveaux chats
            chats_map = self._import_new_chats(chat_conn, cursor["last_date"])

            # 3. Chat_handles
            self._import_chat_handles(chat_conn, handles_map, chats_map)

            # 4. Nouveaux messages
            msg_result = self._import_messages_since(chat_conn, last_rowid, handles_map, chats_map)
            result.total_messages = msg_result["imported"]
            result.total_skipped = msg_result["skipped"]
            result.total_failed = msg_result["failed"]
            result.errors = msg_result.get("errors", [])

            # 5. Nouveaux attachments
            att_batch = self._import_new_attachments(chat_conn, last_rowid)
            result.total_attachments = att_batch["imported"]

            # 6. Nouvelles reactions
            reac_batch = self._import_reactions_since(chat_conn, last_rowid, handles_map)
            result.total_reactions = reac_batch["imported"]

            self._close_chat_db()

            # Mise a jour du curseur
            self._update_cursor(
                last_rowid=chat_max_rowid,
                total_imported=result.total_messages,
                total_failed=result.total_failed,
                status="idle",
            )

            result.reconciliation = self.reconcile().__dict__

        except Exception as e:
            logger.exception("[imessage_import] Echec sync incrementale")
            self._update_cursor(
                last_rowid=last_rowid,
                status="error",
                error_message=f"{type(e).__name__}: {e}",
            )
            result.errors.append(f"Erreur fatale : {type(e).__name__}: {e}")
        finally:
            self._close_chat_db()

        result.duration_seconds = (datetime.now() - t0).total_seconds()
        result.completed_at = datetime.now(timezone.utc).isoformat()
        return result

    # ── Helpers chat.db ────────────────────────────────────────

    def _get_max_chat_rowid(self) -> int:
        conn = self._open_chat_db()
        try:
            row = conn.execute("SELECT COALESCE(MAX(ROWID), 0) m FROM message").fetchone()
            return int(row["m"]) if row else 0
        finally:
            conn.close()

    # ── Import handles ─────────────────────────────────────────

    def _import_handles(self, chat_conn: sqlite3.Connection) -> dict[int, int]:
        """Importe tous les handles. Retourne {apple_handle_id: jarvis_handle_id}."""
        rows = chat_conn.execute(
            "SELECT ROWID, id, country, service, uncanonicalized_id FROM handle"
        ).fetchall()
        mapping: dict[int, int] = {}
        with get_db() as jarvis_conn:
            for r in rows:
                jarvis_id = self._upsert_handle(
                    jarvis_conn,
                    apple_handle_id=r["ROWID"],
                    handle=str(r["id"] or ""),
                    country=str(r["country"] or "") or None,
                    service=str(r["service"] or "") or None,
                    uncanonicalized_id=str(r["uncanonicalized_id"] or "") or None,
                )
                if jarvis_id:
                    mapping[r["ROWID"]] = jarvis_id
        return mapping

    def _import_new_handles(
        self, chat_conn: sqlite3.Connection, since_date: int
    ) -> dict[int, int]:
        """Importe les handles potentiellement nouveaux (heuristic: tous)."""
        return self._import_handles(chat_conn)

    def _upsert_handle(
        self,
        conn: sqlite3.Connection,
        apple_handle_id: int,
        handle: str,
        country: str | None = None,
        service: str | None = None,
        uncanonicalized_id: str | None = None,
    ) -> int | None:
        """INSERT OR IGNORE un handle. Retourne l'id jarvis."""
        if not handle:
            return None
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO imessage_handles
                   (apple_handle_id, handle, country, service, uncanonicalized_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (apple_handle_id, handle, country, service or "iMessage", uncanonicalized_id),
            )
            if cur.lastrowid:
                return cur.lastrowid
            # Deja existant — recuperer l'id
            row = conn.execute(
                "SELECT id FROM imessage_handles WHERE apple_handle_id = ?",
                (apple_handle_id,),
            ).fetchone()
            return row["id"] if row else None
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT id FROM imessage_handles WHERE apple_handle_id = ?",
                (apple_handle_id,),
            ).fetchone()
            return row["id"] if row else None

    # ── Import chats ───────────────────────────────────────────

    def _import_chats(self, chat_conn: sqlite3.Connection) -> dict[int, int]:
        """Importe tous les chats. Retourne {apple_chat_id: jarvis_chat_id}."""
        rows = chat_conn.execute(
            "SELECT ROWID, chat_identifier, display_name, group_id, style, is_filtered FROM chat"
        ).fetchall()
        mapping: dict[int, int] = {}
        with get_db() as jarvis_conn:
            for r in rows:
                jarvis_id = self._upsert_chat(
                    jarvis_conn,
                    apple_chat_id=r["ROWID"],
                    chat_identifier=str(r["chat_identifier"] or "") or None,
                    display_name=str(r["display_name"] or "") or None,
                    group_id=str(r["group_id"] or "") or None,
                    style=int(r["style"] or 0),
                    is_filtered=int(r["is_filtered"] or 0),
                )
                if jarvis_id:
                    mapping[r["ROWID"]] = jarvis_id
        return mapping

    def _import_new_chats(
        self, chat_conn: sqlite3.Connection, since_date: int
    ) -> dict[int, int]:
        return self._import_chats(chat_conn)

    def _upsert_chat(
        self,
        conn: sqlite3.Connection,
        apple_chat_id: int,
        chat_identifier: str | None,
        display_name: str | None,
        group_id: str | None,
        style: int,
        is_filtered: int,
    ) -> int | None:
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO imessage_chats
                   (apple_chat_id, chat_identifier, display_name, group_id, style, is_filtered)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (apple_chat_id, chat_identifier, display_name, group_id, style, is_filtered),
            )
            if cur.lastrowid:
                return cur.lastrowid
            row = conn.execute(
                "SELECT id FROM imessage_chats WHERE apple_chat_id = ?", (apple_chat_id,),
            ).fetchone()
            return row["id"] if row else None
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT id FROM imessage_chats WHERE apple_chat_id = ?", (apple_chat_id,),
            ).fetchone()
            return row["id"] if row else None

    # ── Import chat_handles ────────────────────────────────────

    def _import_chat_handles(
        self,
        chat_conn: sqlite3.Connection,
        handles_map: dict[int, int],
        chats_map: dict[int, int],
    ) -> int:
        """Importe les liens chat-handle (table chat_handle_join de chat.db)."""
        rows = chat_conn.execute(
            "SELECT chat_id, handle_id FROM chat_handle_join"
        ).fetchall()
        count = 0
        with get_db() as jarvis_conn:
            for r in rows:
                apple_chat_id = r["chat_id"]
                apple_handle_id = r["handle_id"]
                jarvis_chat_id = chats_map.get(apple_chat_id)
                jarvis_handle_id = handles_map.get(apple_handle_id)
                if not jarvis_chat_id or not jarvis_handle_id:
                    continue
                try:
                    jarvis_conn.execute(
                        """INSERT OR IGNORE INTO imessage_chat_handles (chat_id, handle_id)
                           VALUES (?, ?)""",
                        (jarvis_chat_id, jarvis_handle_id),
                    )
                    count += 1
                except sqlite3.IntegrityError:
                    pass
        return count

    # ── Import messages ────────────────────────────────────────

    def _import_all_messages(
        self,
        chat_conn: sqlite3.Connection,
        handles_map: dict[int, int],
        chats_map: dict[int, int],
    ) -> dict[str, Any]:
        """Importe tous les messages par batches depuis ROWID 0."""
        max_rowid = chat_conn.execute(
            "SELECT COALESCE(MAX(ROWID), 0) m FROM message"
        ).fetchone()["m"]
        return self._import_message_batch(
            chat_conn, handles_map, chats_map,
            from_rowid=0, to_rowid=max_rowid,
        )

    def _import_messages_since(
        self,
        chat_conn: sqlite3.Connection,
        since_rowid: int,
        handles_map: dict[int, int],
        chats_map: dict[int, int],
    ) -> dict[str, Any]:
        """Importe les messages depuis since_rowid."""
        max_rowid = chat_conn.execute(
            "SELECT COALESCE(MAX(ROWID), 0) m FROM message"
        ).fetchone()["m"]
        if max_rowid <= since_rowid:
            return {"imported": 0, "skipped": 0, "failed": 0, "errors": []}
        return self._import_message_batch(
            chat_conn, handles_map, chats_map,
            from_rowid=since_rowid + 1, to_rowid=max_rowid,
        )

    def _import_message_batch(
        self,
        chat_conn: sqlite3.Connection,
        handles_map: dict[int, int],
        chats_map: dict[int, int],
        from_rowid: int,
        to_rowid: int,
    ) -> dict[str, Any]:
        """Importe les messages de [from_rowid, to_rowid] par batches de batch_size."""
        total_imported = 0
        total_skipped = 0
        total_failed = 0
        errors: list[str] = []
        current = from_rowid

        while current <= to_rowid:
            batch_end = min(current + self.batch_size - 1, to_rowid)
            try:
                rows = chat_conn.execute(
                    """SELECT ROWID, guid, text, handle_id, date, date_read,
                              is_from_me, is_read, item_type, group_title,
                              associated_message_guid, associated_message_type,
                              cache_roomnames
                       FROM message
                       WHERE ROWID BETWEEN ? AND ?
                         AND text IS NOT NULL
                       ORDER BY ROWID ASC""",
                    (current, batch_end),
                ).fetchall()

                with get_db() as jarvis_conn:
                    for r in rows:
                        try:
                            inserted = self._insert_message(
                                jarvis_conn,
                                apple_rowid=r["ROWID"],
                                guid=str(r["guid"] or ""),
                                apple_handle_id=r["handle_id"],
                                handles_map=handles_map,
                                apple_chat_roomname=str(r["cache_roomnames"] or ""),
                                chats_map=chats_map,
                                text=str(r["text"] or ""),
                                date=int(r["date"] or 0),
                                date_read=int(r["date_read"] or 0),
                                is_from_me=int(r["is_from_me"] or 0),
                                is_read=int(r["is_read"] or 0),
                                item_type=int(r["item_type"] or 0),
                                group_title=str(r["group_title"] or "") or None,
                                associated_message_guid=str(r["associated_message_guid"] or "") or None,
                                associated_message_type=int(r["associated_message_type"] or 0),
                            )
                            if inserted:
                                total_imported += 1
                            else:
                                total_skipped += 1
                        except Exception as e:
                            total_failed += 1
                            err = f"Message ROWID={r['ROWID']}: {type(e).__name__}: {e}"
                            errors.append(err)
                            if len(errors) <= 10:
                                logger.error("[imessage_import] %s", err)

                logger.debug(
                    "[imessage_import] Batch %d-%d : %d importes",
                    current, batch_end, total_imported,
                )

            except sqlite3.Error as e:
                total_failed += 1
                errors.append(f"Batch {current}-{batch_end}: {e}")
                logger.error("[imessage_import] Echec batch %d-%d : %s", current, batch_end, e)

            current = batch_end + 1

        return {
            "imported": total_imported,
            "skipped": total_skipped,
            "failed": total_failed,
            "errors": errors,
        }

    def _resolve_chat_id(
        self,
        jarvis_conn: sqlite3.Connection,
        apple_chat_roomname: str,
        chats_map: dict[int, int],
        apple_handle_id: int | None,
    ) -> int | None:
        """Resout le chat_id pour un message.

        Le cache_roomnames de chat.db contient le chat_identifier.
        On essaie :
          1. Par chat_identifier via la colonne chat_identifier de imessage_chats
          2. Par le handle_id + chat_table si c'est un chat solo (1 handle)
        """
        # Chercher par chat_identifier
        if apple_chat_roomname:
            row = jarvis_conn.execute(
                "SELECT id FROM imessage_chats WHERE chat_identifier = ?",
                (apple_chat_roomname,),
            ).fetchone()
            if row:
                return row["id"]

        # Si pas trouve, chercher le chat qui contient ce handle seul
        if apple_handle_id and apple_handle_id in chats_map:
            return chats_map.get(apple_handle_id)

        return None

    def _insert_message(
        self,
        conn: sqlite3.Connection,
        apple_rowid: int,
        guid: str,
        apple_handle_id: int | None,
        handles_map: dict[int, int],
        apple_chat_roomname: str,
        chats_map: dict[int, int],
        text: str,
        date: int,
        date_read: int,
        is_from_me: int,
        is_read: int,
        item_type: int,
        group_title: str | None,
        associated_message_guid: str | None,
        associated_message_type: int,
    ) -> bool:
        """Insere ou ignore un message. Retourne True si insere, False si skip."""
        content_hash = _compute_content_hash(date, apple_handle_id, text, guid)

        # Verifications pre-insert (deduplication)
        existing = conn.execute(
            "SELECT id FROM imessage_messages WHERE apple_rowid = ?",
            (apple_rowid,),
        ).fetchone()
        if existing:
            return False  # deja present par ROWID

        existing = conn.execute(
            "SELECT id FROM imessage_messages WHERE guid = ?",
            (guid,),
        ).fetchone()
        if existing:
            # Mettre a jour les metadonnees si necessaire
            conn.execute(
                """UPDATE imessage_messages
                   SET apple_rowid = ?, date_read = ?, is_read = ?, is_from_me = ?
                   WHERE guid = ? AND apple_rowid IS NULL OR apple_rowid != ?""",
                (apple_rowid, date_read, is_read, is_from_me, guid, apple_rowid),
            )
            return False  # deja present par GUID

        existing = conn.execute(
            "SELECT id FROM imessage_messages WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if existing:
            return False  # deja present par hash

        # Resolution du handle jarvis
        jarvis_handle_id = handles_map.get(apple_handle_id) if apple_handle_id else None

        # Resolution du chat jarvis
        jarvis_chat_id = self._resolve_chat_id(
            conn, apple_chat_roomname, chats_map, apple_handle_id,
        )

        try:
            conn.execute(
                """INSERT INTO imessage_messages
                   (apple_rowid, guid, chat_id, handle_id, text, date, date_read,
                    is_from_me, is_read, item_type, group_title,
                    associated_message_guid, associated_message_type, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    apple_rowid, guid, jarvis_chat_id, jarvis_handle_id,
                    text, date, date_read, is_from_me, is_read, item_type,
                    group_title, associated_message_guid, associated_message_type,
                    content_hash,
                ),
            )
            return True
        except sqlite3.IntegrityError as e:
            err_str = str(e).lower()
            if "unique" in err_str:
                return False  # contrainte UNIQUE violee → skip
            raise

    # ── Import attachments ─────────────────────────────────────

    def _import_attachments(self, chat_conn: sqlite3.Connection) -> dict[str, int]:
        """Importe tous les attachments."""
        rows = chat_conn.execute(
            """SELECT a.ROWID, a.guid, a.filename, a.mime_type, a.transfer_name,
                      a.total_bytes, a.is_outgoing, a.hide_attachment, a.created_date
               FROM attachment a"""
        ).fetchall()
        imported = 0
        with get_db() as jarvis_conn:
            for r in rows:
                try:
                    cur = jarvis_conn.execute(
                        """INSERT OR IGNORE INTO imessage_attachments
                           (apple_attachment_id, guid, filename, mime_type,
                            transfer_name, total_bytes, is_outgoing,
                            hide_attachment, created_date)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            r["ROWID"],
                            str(r["guid"] or ""),
                            str(r["filename"] or ""),
                            str(r["mime_type"] or ""),
                            str(r["transfer_name"] or ""),
                            r["total_bytes"],
                            int(r["is_outgoing"] or 0),
                            int(r["hide_attachment"] or 0),
                            r["created_date"],
                        ),
                    )
                    if cur.rowcount > 0:
                        imported += 1
                except sqlite3.IntegrityError:
                    pass

        # Liens message_attachment_join
        link_rows = chat_conn.execute(
            "SELECT message_id, attachment_id FROM message_attachment_join"
        ).fetchall()
        linked = 0
        with get_db() as jarvis_conn:
            for r in link_rows:
                msg_row = jarvis_conn.execute(
                    "SELECT id FROM imessage_messages WHERE apple_rowid = ?",
                    (r["message_id"],),
                ).fetchone()
                att_row = jarvis_conn.execute(
                    "SELECT id FROM imessage_attachments WHERE apple_attachment_id = ?",
                    (r["attachment_id"],),
                ).fetchone()
                if msg_row and att_row:
                    try:
                        jarvis_conn.execute(
                            """INSERT OR IGNORE INTO imessage_message_attachments
                               (message_id, attachment_id) VALUES (?, ?)""",
                            (msg_row["id"], att_row["id"]),
                        )
                        linked += 1
                    except sqlite3.IntegrityError:
                        pass

        return {"imported": imported, "linked": linked}

    def _import_new_attachments(
        self, chat_conn: sqlite3.Connection, since_rowid: int
    ) -> dict[str, int]:
        """Importe les nouveaux attachments via les nouveaux messages."""
        rows = chat_conn.execute(
            """SELECT a.ROWID, a.guid, a.filename, a.mime_type, a.transfer_name,
                      a.total_bytes, a.is_outgoing, a.hide_attachment, a.created_date
               FROM attachment a
               JOIN message_attachment_join maj ON maj.attachment_id = a.ROWID
               WHERE maj.message_id > ?""",
            (since_rowid,),
        ).fetchall()
        imported = 0
        with get_db() as jarvis_conn:
            for r in rows:
                try:
                    cur = jarvis_conn.execute(
                        """INSERT OR IGNORE INTO imessage_attachments
                           (apple_attachment_id, guid, filename, mime_type,
                            transfer_name, total_bytes, is_outgoing,
                            hide_attachment, created_date)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            r["ROWID"],
                            str(r["guid"] or ""),
                            str(r["filename"] or ""),
                            str(r["mime_type"] or ""),
                            str(r["transfer_name"] or ""),
                            r["total_bytes"],
                            int(r["is_outgoing"] or 0),
                            int(r["hide_attachment"] or 0),
                            r["created_date"],
                        ),
                    )
                    if cur.rowcount > 0:
                        imported += 1
                except sqlite3.IntegrityError:
                    pass
        return {"imported": imported, "linked": 0}

    # ── Import reactions ───────────────────────────────────────

    def _import_reactions(
        self,
        chat_conn: sqlite3.Connection,
        handles_map: dict[int, int],
    ) -> dict[str, int]:
        """Importe les reactions (tapbacks iMessage).

        Dans chat.db, les reactions sont des messages avec associated_message_type
        dans la plage 2000-2999 (les tapbacks) ou des lignes dediees.
        On lit les messages avec associated_message_guid ET associated_message_type > 0.
        """
        rows = chat_conn.execute(
            """SELECT ROWID, handle_id, associated_message_guid, associated_message_type, is_from_me
               FROM message
               WHERE associated_message_type > 0
                 AND associated_message_guid IS NOT NULL"""
        ).fetchall()

        imported = 0
        with get_db() as jarvis_conn:
            for r in rows:
                # Trouver le message cible (le message auquel on reagit)
                target = jarvis_conn.execute(
                    "SELECT id FROM imessage_messages WHERE guid = ?",
                    (r["associated_message_guid"],),
                ).fetchone()
                if not target:
                    continue

                reactor_jarvis_id = handles_map.get(r["handle_id"])
                if not reactor_jarvis_id:
                    continue

                reaction_type = int(r["associated_message_type"] or 0)
                # Reaction_type 2000-2999 = iMessage tapbacks
                if reaction_type < 2000 or reaction_type > 2999:
                    continue

                try:
                    cur = jarvis_conn.execute(
                        """INSERT OR IGNORE INTO imessage_reactions
                           (message_id, reactor_handle_id, reaction_type, apple_associated_guid)
                           VALUES (?, ?, ?, ?)""",
                        (
                            target["id"],
                            reactor_jarvis_id,
                            reaction_type,
                            r["associated_message_guid"],
                        ),
                    )
                    if cur.rowcount > 0:
                        imported += 1
                except sqlite3.IntegrityError:
                    pass

        return {"imported": imported}

    def _import_reactions_since(
        self,
        chat_conn: sqlite3.Connection,
        since_rowid: int,
        handles_map: dict[int, int],
    ) -> dict[str, int]:
        """Importe les nouvelles reactions depuis since_rowid."""
        rows = chat_conn.execute(
            """SELECT ROWID, handle_id, associated_message_guid, associated_message_type, is_from_me
               FROM message
               WHERE ROWID > ?
                 AND associated_message_type > 0
                 AND associated_message_guid IS NOT NULL""",
            (since_rowid,),
        ).fetchall()

        imported = 0
        with get_db() as jarvis_conn:
            for r in rows:
                target = jarvis_conn.execute(
                    "SELECT id FROM imessage_messages WHERE guid = ?",
                    (r["associated_message_guid"],),
                ).fetchone()
                if not target:
                    continue
                reactor_jarvis_id = handles_map.get(r["handle_id"])
                if not reactor_jarvis_id:
                    continue
                reaction_type = int(r["associated_message_type"] or 0)
                if reaction_type < 2000 or reaction_type > 2999:
                    continue
                try:
                    cur = jarvis_conn.execute(
                        """INSERT OR IGNORE INTO imessage_reactions
                           (message_id, reactor_handle_id, reaction_type, apple_associated_guid)
                           VALUES (?, ?, ?, ?)""",
                        (target["id"], reactor_jarvis_id, reaction_type, r["associated_message_guid"]),
                    )
                    if cur.rowcount > 0:
                        imported += 1
                except sqlite3.IntegrityError:
                    pass

        return {"imported": imported}

    # ── Reconciliation ─────────────────────────────────────────

    def reconcile(self) -> ReconciliationReport:
        """Audit post-import : compare chat.db et jarvis.db, corrige si possible."""
        report = ReconciliationReport()

        if not self.is_available():
            report.ok = False
            return report

        chat_conn = self._open_chat_db()
        try:
            # Comptages chat.db
            report.chat_db_messages = chat_conn.execute(
                "SELECT COUNT(*) c FROM message WHERE text IS NOT NULL"
            ).fetchone()["c"]
            report.chat_db_chats = chat_conn.execute(
                "SELECT COUNT(*) c FROM chat"
            ).fetchone()["c"]
            report.chat_db_handles = chat_conn.execute(
                "SELECT COUNT(*) c FROM handle"
            ).fetchone()["c"]
        finally:
            chat_conn.close()

        with get_db() as jarvis_conn:
            report.jarvis_db_messages = jarvis_conn.execute(
                "SELECT COUNT(*) c FROM imessage_messages"
            ).fetchone()["c"]
            report.jarvis_db_chats = jarvis_conn.execute(
                "SELECT COUNT(*) c FROM imessage_chats"
            ).fetchone()["c"]
            report.jarvis_db_handles = jarvis_conn.execute(
                "SELECT COUNT(*) c FROM imessage_handles"
            ).fetchone()["c"]

            # Messages orphelins : chat_id ou handle_id NULL ou inexistant
            report.orphan_messages = jarvis_conn.execute(
                """SELECT COUNT(*) c FROM imessage_messages m
                   WHERE m.chat_id IS NULL
                      OR m.handle_id IS NULL
                      OR m.chat_id NOT IN (SELECT id FROM imessage_chats)
                      OR m.handle_id NOT IN (SELECT id FROM imessage_handles)"""
            ).fetchone()["c"]

            # Tentative de correction : reassocier les messages orphelins
            if report.orphan_messages > 0:
                jarvis_conn.execute(
                    """UPDATE imessage_messages SET chat_id = NULL
                       WHERE chat_id IS NOT NULL
                         AND chat_id NOT IN (SELECT id FROM imessage_chats)"""
                )
                jarvis_conn.execute(
                    """UPDATE imessage_messages SET handle_id = NULL
                       WHERE handle_id IS NOT NULL
                         AND handle_id NOT IN (SELECT id FROM imessage_handles)"""
                )
                report.orphan_fixed = report.orphan_messages - jarvis_conn.execute(
                    """SELECT COUNT(*) c FROM imessage_messages m
                       WHERE m.chat_id IS NULL OR m.handle_id IS NULL"""
                ).fetchone()["c"]

            # Doublons (meme guid ou meme apple_rowid)
            report.duplicates_found = jarvis_conn.execute(
                """SELECT COUNT(*) c FROM (
                       SELECT guid, COUNT(*) cnt FROM imessage_messages
                       GROUP BY guid HAVING cnt > 1
                   )"""
            ).fetchone()["c"]
            if report.duplicates_found > 0:
                # Supprimer les doublons : garder le plus ancien (id le plus petit)
                jarvis_conn.execute(
                    """DELETE FROM imessage_messages WHERE id NOT IN (
                           SELECT MIN(id) FROM imessage_messages GROUP BY guid
                       )"""
                )
                remaining = jarvis_conn.execute(
                    """SELECT COUNT(*) c FROM (
                           SELECT guid, COUNT(*) cnt FROM imessage_messages
                           GROUP BY guid HAVING cnt > 1
                       )"""
                ).fetchone()["c"]
                report.duplicates_removed = report.duplicates_found - remaining

        # Considerer OK si les DBs sont vides, ou si le taux de couverture est >= 98%
        # et qu'il y a moins de 1% d'orphelins
        if report.chat_db_messages == 0 and report.jarvis_db_messages == 0:
            report.ok = True
        else:
            report.ok = (
                report.jarvis_db_messages >= report.chat_db_messages * 0.98
                and (
                    report.jarvis_db_messages == 0
                    or report.orphan_messages < report.jarvis_db_messages * 0.01
                )
            )

        if report.ok:
            logger.info(
                "[imessage_import] Reconciliation OK — "
                "%d messages (chat.db=%d), %d orphelins fixes, %d doublons supprimes",
                report.jarvis_db_messages, report.chat_db_messages,
                report.orphan_fixed, report.duplicates_removed,
            )
        else:
            logger.warning(
                "[imessage_import] Reconciliation : ecart detecte — "
                "chat.db=%d messages, jarvis.db=%d messages",
                report.chat_db_messages, report.jarvis_db_messages,
            )

        return report


imessage_importer = IMessageImporter()

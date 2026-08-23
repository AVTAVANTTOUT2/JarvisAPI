"""Moteur d'import iMessage — importe chat.db dans jarvis.db.

Import idempotent, incremental, avec deduplication par apple_rowid puis guid.
content_hash inclut apple_rowid : deux lignes distinctes ne se masquent pas.

Architecture :
  - Import initial : lit tout chat.db par batches, importe handles → chats → messages → attachments → reactions
  - Sync incrementale : repart du curseur imessage_sync_cursor, ne lit que ROWID > last_apple_rowid
  - Reconciliation : réparation — rowids importables manquants, dates périmées, tapbacks
"""

from __future__ import annotations

import hashlib
import fcntl
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import config
from database import (
    current_profile_id,
    get_db,
    normalize_contact_identity,
    profile_database_path,
)
from integrations.apple_data import (
    DEFAULT_CHAT_DB_PATH,
    AppleDataService,
    apple_data,
    apple_epoch_to_datetime,
)
from integrations.imessage_body import message_text_from_row

_ASSOCIATED_GUID_PREFIX = ("p:", "P:")


def normalize_associated_message_guid(guid: str | None) -> str:
    """Retire le préfixe Apple ``p:0/<guid>`` des tapbacks."""
    value = str(guid or "").strip()
    if not value:
        return ""
    if "/" in value and value[:2] in _ASSOCIATED_GUID_PREFIX:
        return value.rsplit("/", 1)[-1].strip()
    return value

# Une seule sync à la fois (worker d'ingestion + diagnostic HTTP).
# Un event pendant le lock pose un dirty flag plutôt que d'être jeté.
_SYNC_LOCK = threading.Lock()
_DIRTY = threading.Event()
_MAX_DIRTY_LOOPS = 8
_AVAILABILITY_FAILURE_COOLDOWN = 30.0


def _try_acquire_process_sync_lock():
    """Verrou inter-processus profilé pour daemon, API et worker durable."""

    db_path = profile_database_path(current_profile_id())
    lock_path = db_path.parent / f".{db_path.name}.imessage-sync.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    handle = os.fdopen(fd, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except BlockingIOError:
        handle.close()
        return None


def _skipped_already_running() -> ImportResult:
    skipped = ImportResult(mode="incremental")
    skipped.errors.append("sync_already_running")
    return skipped


def _requeue_dirty_sync() -> None:
    """Le cycle courant n'a pas tout vu : un job d'ingestion reprendra."""

    try:
        from database.ingestion import ConnectorBindingRequired, enqueue_ingestion_job

        enqueue_ingestion_job("imessage", job_kind="sync", dedupe_key="sync:dirty")
    except ConnectorBindingRequired:
        logger.info("[imessage_import] cycle dirty, connecteur non lié")
    except Exception:
        logger.exception("[imessage_import] réenfilement dirty impossible")


def _merge_incremental_results(acc: ImportResult, nxt: ImportResult) -> ImportResult:
    acc.total_messages += nxt.total_messages
    acc.total_skipped += nxt.total_skipped
    acc.total_failed += nxt.total_failed
    acc.total_handles += nxt.total_handles
    acc.total_chats += nxt.total_chats
    acc.total_attachments += nxt.total_attachments
    acc.total_reactions += nxt.total_reactions
    acc.errors.extend(nxt.errors)
    if nxt.reconciliation:
        acc.reconciliation = nxt.reconciliation
    acc.duration_seconds += nxt.duration_seconds
    acc.completed_at = nxt.completed_at or acc.completed_at
    return acc


def _chat_message_sql_parts(conn: sqlite3.Connection) -> dict[str, str]:
    """Fragments SQL partagés : lecture batch et inventaire léger (sans BLOB)."""
    tables = {
        str(row["name"] if isinstance(row, sqlite3.Row) else row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    message_columns = {
        str(row["name"] if isinstance(row, sqlite3.Row) else row[1])
        for row in conn.execute("PRAGMA table_info(message)")
    }
    has_body = "attributedBody" in message_columns
    has_chat_join = "chat_message_join" in tables
    has_handle_join = has_chat_join and "chat_handle_join" in tables
    has_attachment_join = "message_attachment_join" in tables

    body_filter = " OR m.attributedBody IS NOT NULL" if has_body else ""
    attachment_filter = (
        " OR EXISTS(SELECT 1 FROM message_attachment_join maj "
        "WHERE maj.message_id = m.ROWID)"
        if has_attachment_join
        else ""
    )
    resolved_handle = "m.handle_id"
    if has_handle_join:
        resolved_handle = """CASE
               WHEN m.handle_id IS NULL OR m.handle_id = 0 THEN (
                   SELECT chj.handle_id
                   FROM chat_message_join cmj
                   JOIN chat_handle_join chj ON chj.chat_id = cmj.chat_id
                   WHERE cmj.message_id = m.ROWID
                   ORDER BY chj.handle_id
                   LIMIT 1
               )
               ELSE m.handle_id
           END"""

    resolved_chat = "NULLIF(TRIM(m.cache_roomnames), '')"
    if has_chat_join and "chat" in tables:
        resolved_chat = """COALESCE(
               NULLIF(TRIM(m.cache_roomnames), ''),
               (
                   SELECT c.chat_identifier
                   FROM chat_message_join cmj
                   JOIN chat c ON c.ROWID = cmj.chat_id
                   WHERE cmj.message_id = m.ROWID
                   ORDER BY cmj.chat_id
                   LIMIT 1
               )
           )"""

    return {
        "attributed_body": (
            "m.attributedBody" if has_body else "NULL AS attributedBody"
        ),
        "resolved_handle": resolved_handle,
        "resolved_chat": resolved_chat,
        "has_attachment": (
            "EXISTS(SELECT 1 FROM message_attachment_join maj "
            "WHERE maj.message_id = m.ROWID)"
            if has_attachment_join
            else "0"
        ),
        "importable_predicate": f"(m.text IS NOT NULL{body_filter}{attachment_filter})",
    }


def _message_batch_sql(conn: sqlite3.Connection) -> str:
    """Construit la lecture compatible avec plusieurs générations de chat.db."""
    parts = _chat_message_sql_parts(conn)
    return f"""
        SELECT m.ROWID,
               m.guid,
               m.text,
               {parts["attributed_body"]},
               m.handle_id,
               m.date,
               m.date_read,
               m.is_from_me,
               m.is_read,
               m.item_type,
               m.group_title,
               m.associated_message_guid,
               m.associated_message_type,
               m.cache_roomnames,
               {parts["has_attachment"]} AS has_attachment,
               {parts["resolved_handle"]} AS resolved_handle_id,
               {parts["resolved_chat"]} AS resolved_chat_identifier
        FROM message m
        WHERE m.ROWID BETWEEN ? AND ?
          AND {parts["importable_predicate"]}
        ORDER BY m.ROWID ASC
    """


def _count_importable_messages(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) AS count FROM ({_message_batch_sql(conn)})",
        (0, 9_223_372_036_854_775_807),
    ).fetchone()
    return int(row["count"] if row else 0)


logger = logging.getLogger(__name__)

CHAT_DB_PATH = DEFAULT_CHAT_DB_PATH

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

    mode: str = "initial"  # "initial" ou "incremental"
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
    missing_imported: int = 0
    refreshed: int = 0
    reactions_imported: int = 0
    ok: bool = False


def _apple_ts_to_iso(ts: int | float | None) -> str | None:
    """Convertit un timestamp Apple (secondes depuis 2001-01-01) en ISO 8601."""
    if ts is None:
        return None
    converted = apple_epoch_to_datetime(ts, zero_is_none=False)
    if converted is None:
        return None
    if converted.tzinfo is None:
        converted = converted.replace(tzinfo=timezone.utc)
    return converted.astimezone(timezone.utc).isoformat()


def _compute_content_hash(
    date_raw: int | None,
    handle_id: int | None,
    text: str | None,
    guid: str | None,
    apple_rowid: int | None = None,
) -> str:
    """Calcule un hash SHA256 unique pour un message.

    Combine date brute (timestamp Apple), handle_id (ROWID du handle dans chat.db),
    texte, guid et apple_rowid pour produire une empreinte stable par ligne.
    """
    components = [
        str(date_raw or 0),
        str(handle_id or 0),
        (text or "").strip(),
        (guid or "").strip(),
        str(apple_rowid or 0),
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

    def __init__(
        self,
        batch_size: int = DEFAULT_BATCH_SIZE,
        data_service: AppleDataService | None = None,
    ) -> None:
        self.batch_size = batch_size
        self._chat_db_conn: sqlite3.Connection | None = None
        self._available: bool | None = None
        self._last_failed_check = 0.0
        self._apple_data_override = data_service

    def _data_service(self) -> AppleDataService:
        """Résout le service, tout en conservant le monkeypatch historique du chemin."""
        return self._apple_data_override or apple_data.with_db_path(CHAT_DB_PATH)

    # ── Disponibilite ──────────────────────────────────────────

    def is_available(self) -> bool:
        """Verifie l'acces a chat.db en lecture seule."""
        if self._available is True:
            return True
        if (
            self._available is False
            and time.monotonic() - self._last_failed_check
            < _AVAILABILITY_FAILURE_COOLDOWN
        ):
            return False
        service = self._data_service()
        if not service.db_path.exists():
            logger.warning(
                "[imessage_import] chat.db introuvable : %s", service.db_path
            )
            self._available = False
            self._last_failed_check = time.monotonic()
            return False
        try:
            service.count_messages()
            self._available = True
            self._last_failed_check = 0.0
            logger.info("[imessage_import] chat.db accessible en lecture")
        except (sqlite3.OperationalError, jarvis_sqlite.OperationalError) as e:
            logger.warning(
                "[imessage_import] chat.db inaccessible : %s — "
                "Full Disk Access requis pour l'app qui lance JARVIS.",
                e,
            )
            self._available = False
            self._last_failed_check = time.monotonic()
        return self._available

    def reset_availability_cache(self) -> None:
        """Force un nouveau probe après une modification de permission macOS."""

        self._available = None
        self._last_failed_check = 0.0

    def _open_chat_db(self) -> sqlite3.Connection:
        """Ouvre chat.db en lecture seule."""
        self._close_chat_db()
        self._chat_db_conn = self._data_service().connect_readonly(timeout=10.0)
        return self._chat_db_conn

    def _close_chat_db(self) -> None:
        if self._chat_db_conn:
            try:
                self._chat_db_conn.close()
            except (sqlite3.Error, jarvis_sqlite.Error):
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
        last_date: int | None = None,
        last_guid: str | None = None,
        total_imported: int | None = None,
        total_failed: int | None = None,
        status: str = "idle",
        error_message: str = "",
    ) -> None:
        """Met a jour le curseur de synchronisation."""
        with get_db() as conn:
            cur = conn.execute(
                "SELECT id FROM imessage_sync_cursor WHERE id = 1"
            ).fetchone()
            if cur:
                conn.execute(
                    """UPDATE imessage_sync_cursor
                       SET last_apple_rowid = ?,
                           last_date = COALESCE(?, last_date),
                           last_guid = COALESCE(?, last_guid),
                           total_imported = COALESCE(?, total_imported),
                           total_failed = COALESCE(?, total_failed),
                           last_sync_at = CURRENT_TIMESTAMP,
                           completed_at = CASE WHEN ? = 'idle' THEN CURRENT_TIMESTAMP ELSE completed_at END,
                           status = ?,
                           error_message = ?
                       WHERE id = 1""",
                    (
                        last_rowid,
                        last_date,
                        last_guid,
                        total_imported,
                        total_failed,
                        status,
                        status,
                        error_message,
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO imessage_sync_cursor
                       (id, last_apple_rowid, last_date, last_guid, total_imported,
                        total_failed, started_at, status, error_message)
                       VALUES (1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)""",
                    (
                        last_rowid,
                        last_date or 0,
                        last_guid or "",
                        total_imported or 0,
                        total_failed or 0,
                        status,
                        error_message,
                    ),
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
            safe_rowid = max(
                int(cursor["last_apple_rowid"] or 0),
                int(msg_result.get("last_contiguous_rowid", 0)),
            )
            safe_date = cursor["last_date"]
            safe_guid = cursor["last_guid"]
            if safe_rowid:
                last_message = chat_conn.execute(
                    "SELECT date, guid FROM message WHERE ROWID <= ? ORDER BY ROWID DESC LIMIT 1",
                    (safe_rowid,),
                ).fetchone()
                if last_message:
                    safe_date = int(last_message["date"] or safe_date or 0)
                    safe_guid = str(last_message["guid"] or safe_guid or "")
            logger.info(
                "[imessage_import] Messages : %d importes, %d skippes, %d echoues",
                result.total_messages,
                result.total_skipped,
                result.total_failed,
            )

            # 5. Attachments
            logger.info("[imessage_import] Phase 4/5 : attachments...")
            att_result = self._import_attachments(chat_conn)
            result.total_attachments = att_result["imported"]
            logger.info(
                "[imessage_import] %d attachments importes", result.total_attachments
            )

            # 6. Reactions
            logger.info("[imessage_import] Phase 5/5 : reactions...")
            reac_result = self._import_reactions(chat_conn, handles_map)
            result.total_reactions = reac_result["imported"]
            logger.info(
                "[imessage_import] %d reactions importees", result.total_reactions
            )

            self._close_chat_db()

            # Curseur final
            self._update_cursor(
                last_rowid=safe_rowid,
                last_date=safe_date,
                last_guid=safe_guid,
                total_imported=result.total_messages,
                total_failed=result.total_failed,
                status="error" if result.total_failed else "idle",
                error_message="; ".join(result.errors[:3])
                if result.total_failed
                else "",
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
            "[imessage_import] Import termine en %.1fs — %d msg / %d skip / %d erreurs",
            result.duration_seconds,
            result.total_messages,
            result.total_skipped,
            result.total_failed,
        )
        return result

    # ── Sync incrementale ──────────────────────────────────────

    def sync_incremental(self) -> ImportResult:
        """Sync incrementale : uniquement les nouveaux messages depuis le curseur.

        Si un autre cycle tient le verrou, le dirty flag est posé et un job
        ``sync:dirty`` est enfilé — plus aucun event n'est silencieusement jeté.
        """
        if not self.is_available():
            raise RuntimeError("chat.db inaccessible — verifier Full Disk Access")

        acquired = _SYNC_LOCK.acquire(blocking=False)
        if not acquired:
            _DIRTY.set()
            acquired = _SYNC_LOCK.acquire(timeout=0.05)
            if not acquired:
                logger.info(
                    "[imessage_import] Sync déjà en cours — cycle marqué dirty"
                )
                _requeue_dirty_sync()
                return _skipped_already_running()

        process_lock = _try_acquire_process_sync_lock()
        if process_lock is None:
            _DIRTY.set()
            _SYNC_LOCK.release()
            logger.info(
                "[imessage_import] Sync active dans un autre processus — cycle reporté"
            )
            _requeue_dirty_sync()
            return _skipped_already_running()
        try:
            combined = ImportResult(mode="incremental")
            loops = 0
            while True:
                _DIRTY.clear()
                loops += 1
                _merge_incremental_results(combined, self._sync_incremental_locked())
                if not _DIRTY.is_set() or loops >= _MAX_DIRTY_LOOPS:
                    break
            if _DIRTY.is_set():
                _requeue_dirty_sync()
            return combined
        finally:
            fcntl.flock(process_lock.fileno(), fcntl.LOCK_UN)
            process_lock.close()
            _SYNC_LOCK.release()
            if _DIRTY.is_set():
                _requeue_dirty_sync()

    def _sync_incremental_locked(self) -> ImportResult:
        t0 = datetime.now()
        result = ImportResult(mode="incremental")

        cursor = self._get_cursor()
        last_rowid = cursor["last_apple_rowid"]

        chat_max_rowid = self._get_max_chat_rowid()
        if chat_max_rowid <= last_rowid:
            logger.info(
                "[imessage_import] Aucun nouveau message (cursor=%d, max=%d)",
                last_rowid,
                chat_max_rowid,
            )
            result.duration_seconds = (datetime.now() - t0).total_seconds()
            result.completed_at = datetime.now(timezone.utc).isoformat()
            return result

        logger.info(
            "[imessage_import] Sync incrementale : ROWID %d → %d (%d nouveaux)",
            last_rowid,
            chat_max_rowid,
            chat_max_rowid - last_rowid,
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
            msg_result = self._import_messages_since(
                chat_conn, last_rowid, handles_map, chats_map
            )
            result.total_messages = msg_result["imported"]
            result.total_skipped = msg_result["skipped"]
            result.total_failed = msg_result["failed"]
            result.errors = msg_result.get("errors", [])
            safe_rowid = max(
                last_rowid,
                int(msg_result.get("last_contiguous_rowid", last_rowid)),
            )
            safe_date = cursor["last_date"]
            safe_guid = cursor["last_guid"]
            if safe_rowid > last_rowid:
                last_message = chat_conn.execute(
                    "SELECT date, guid FROM message WHERE ROWID <= ? ORDER BY ROWID DESC LIMIT 1",
                    (safe_rowid,),
                ).fetchone()
                if last_message:
                    safe_date = int(last_message["date"] or safe_date or 0)
                    safe_guid = str(last_message["guid"] or safe_guid or "")

            # 5. Nouveaux attachments
            att_batch = self._import_new_attachments(chat_conn, last_rowid)
            result.total_attachments = att_batch["imported"]

            # 6. Nouvelles reactions
            reac_batch = self._import_reactions_since(
                chat_conn, last_rowid, handles_map
            )
            result.total_reactions = reac_batch["imported"]

            self._close_chat_db()

            # Mise a jour du curseur
            self._update_cursor(
                last_rowid=safe_rowid,
                last_date=safe_date,
                last_guid=safe_guid,
                total_imported=result.total_messages,
                total_failed=result.total_failed,
                status="error" if result.total_failed else "idle",
                error_message="; ".join(result.errors[:3])
                if result.total_failed
                else "",
            )

            result.reconciliation = self.reconcile().__dict__

        except Exception as e:
            logger.exception("[imessage_import] Echec sync incrementale")
            self._update_cursor(
                last_rowid=last_rowid,
                status="error",
                error_message=f"{type(e).__name__}: {e}",
            )
            result.total_failed += 1
            result.errors.append(f"Erreur fatale : {type(e).__name__}: {e}")
        finally:
            self._close_chat_db()

        result.duration_seconds = (datetime.now() - t0).total_seconds()
        result.completed_at = datetime.now(timezone.utc).isoformat()
        return result

    # ── Helpers chat.db ────────────────────────────────────────

    def _get_max_chat_rowid(self) -> int:
        return self._data_service().get_max_rowid()

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
                (
                    apple_handle_id,
                    handle,
                    country,
                    service or "iMessage",
                    uncanonicalized_id,
                ),
            )
            jarvis_id = int(cur.lastrowid) if cur.lastrowid else None
        except (sqlite3.IntegrityError, jarvis_sqlite.IntegrityError):
            jarvis_id = None
        if jarvis_id is None:
            row = conn.execute(
                "SELECT id FROM imessage_handles WHERE apple_handle_id = ?",
                (apple_handle_id,),
            ).fetchone()
            jarvis_id = int(row["id"]) if row else None
        if jarvis_id is not None:
            self._enrich_handle_identity(conn, jarvis_id, handle)
        return jarvis_id

    def _ensure_self_handle(
        self,
        conn: sqlite3.Connection,
        handles_map: dict[int, int],
    ) -> int | None:
        """Handle synthétique pour les tapbacks / messages envoyés (handle_id Apple = 0)."""
        cached = handles_map.get(0)
        if cached:
            return cached
        jarvis_id = self._upsert_handle(
            conn,
            apple_handle_id=0,
            handle="__self__",
            service="iMessage",
        )
        if jarvis_id:
            handles_map[0] = jarvis_id
        return jarvis_id

    def _enrich_handle_identity(
        self,
        conn: sqlite3.Connection,
        handle_id: int,
        handle: str,
        *,
        display_name: str | None = None,
    ) -> None:
        """Relie un handle à l'identité Contacts.app sans changer de profil."""

        resolved_name = str(display_name or "").strip()
        if not resolved_name:
            try:
                candidate = str(
                    self._data_service().resolve_handle(handle) or ""
                ).strip()
            except Exception:
                candidate = ""
            if candidate and candidate.casefold() != str(handle).strip().casefold():
                resolved_name = candidate
        identity_type, normalized = normalize_contact_identity("imessage", handle)
        person_id: int | None = None
        if resolved_name:
            person = conn.execute(
                "SELECT id FROM people WHERE name = ? COLLATE NOCASE LIMIT 1",
                (resolved_name,),
            ).fetchone()
            person_id = int(person["id"]) if person else None
        conn.execute(
            """
            INSERT INTO contact_identities(
                identity_type, normalized_value, display_name, person_id,
                source, confidence, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'imessage', 1.0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(identity_type, normalized_value) DO UPDATE SET
                display_name = CASE WHEN excluded.display_name <> ''
                    THEN excluded.display_name ELSE contact_identities.display_name END,
                person_id = COALESCE(excluded.person_id, contact_identities.person_id),
                updated_at = CURRENT_TIMESTAMP
            """,
            (identity_type, normalized, resolved_name, person_id),
        )
        identity = conn.execute(
            "SELECT id, display_name FROM contact_identities "
            "WHERE identity_type = ? AND normalized_value = ?",
            (identity_type, normalized),
        ).fetchone()
        if identity:
            conn.execute(
                """
                UPDATE imessage_handles
                SET contact_identity_id = ?,
                    display_name = CASE WHEN ? <> '' THEN ? ELSE display_name END
                WHERE id = ?
                """,
                (
                    int(identity["id"]),
                    str(identity["display_name"] or ""),
                    str(identity["display_name"] or ""),
                    int(handle_id),
                ),
            )

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
            conn.execute(
                """
                INSERT INTO imessage_chats(
                    apple_chat_id, chat_identifier, display_name, group_id, style, is_filtered
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(apple_chat_id) DO UPDATE SET
                    chat_identifier = COALESCE(excluded.chat_identifier, imessage_chats.chat_identifier),
                    display_name = CASE WHEN COALESCE(excluded.display_name, '') <> ''
                        THEN excluded.display_name ELSE imessage_chats.display_name END,
                    group_id = COALESCE(excluded.group_id, imessage_chats.group_id),
                    style = excluded.style,
                    is_filtered = excluded.is_filtered
                """,
                (
                    apple_chat_id,
                    chat_identifier,
                    display_name,
                    group_id,
                    style,
                    is_filtered,
                ),
            )
        except (sqlite3.IntegrityError, jarvis_sqlite.IntegrityError):
            pass
        row = conn.execute(
            "SELECT id FROM imessage_chats WHERE apple_chat_id = ?",
            (apple_chat_id,),
        ).fetchone()
        jarvis_id = int(row["id"]) if row else None
        if display_name and chat_identifier:
            handle = conn.execute(
                """
                SELECT id, handle FROM imessage_handles
                WHERE handle = ? OR uncanonicalized_id = ?
                ORDER BY id LIMIT 1
                """,
                (chat_identifier, chat_identifier),
            ).fetchone()
            if handle:
                self._enrich_handle_identity(
                    conn,
                    int(handle["id"]),
                    str(handle["handle"]),
                    display_name=display_name,
                )
        return jarvis_id

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
                except (sqlite3.IntegrityError, jarvis_sqlite.IntegrityError):
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
            chat_conn,
            handles_map,
            chats_map,
            from_rowid=0,
            to_rowid=max_rowid,
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
            return {
                "imported": 0,
                "updated": 0,
                "skipped": 0,
                "failed": 0,
                "errors": [],
                "failed_rowids": [],
                "last_contiguous_rowid": since_rowid,
            }
        return self._import_message_batch(
            chat_conn,
            handles_map,
            chats_map,
            from_rowid=since_rowid + 1,
            to_rowid=max_rowid,
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
        total_updated = 0
        errors: list[str] = []
        failed_rowids: list[int] = []
        current = from_rowid

        while current <= to_rowid:
            batch_end = min(current + self.batch_size - 1, to_rowid)
            try:
                rows = chat_conn.execute(
                    _message_batch_sql(chat_conn),
                    (current, batch_end),
                ).fetchall()

                with get_db() as jarvis_conn:
                    for r in rows:
                        try:
                            text = message_text_from_row(
                                r["text"],
                                r["attributedBody"],
                            )
                            has_body = r["attributedBody"] is not None
                            if (
                                not text
                                and not bool(r["has_attachment"])
                                and not has_body
                            ):
                                total_skipped += 1
                                continue

                            resolved_handle = r["resolved_handle_id"]
                            if resolved_handle in (None, 0):
                                resolved_handle = None
                            else:
                                resolved_handle = int(resolved_handle)

                            outcome = self._insert_message(
                                jarvis_conn,
                                apple_rowid=r["ROWID"],
                                guid=str(r["guid"] or ""),
                                apple_handle_id=resolved_handle,
                                handles_map=handles_map,
                                apple_chat_roomname=str(
                                    r["resolved_chat_identifier"] or ""
                                ),
                                chats_map=chats_map,
                                text=text,
                                attributed_body=r["attributedBody"],
                                date=int(r["date"] or 0),
                                date_read=int(r["date_read"] or 0),
                                is_from_me=int(r["is_from_me"] or 0),
                                is_read=int(r["is_read"] or 0),
                                item_type=int(r["item_type"] or 0),
                                group_title=str(r["group_title"] or "") or None,
                                associated_message_guid=str(
                                    r["associated_message_guid"] or ""
                                )
                                or None,
                                associated_message_type=int(
                                    r["associated_message_type"] or 0
                                ),
                            )
                            if outcome == "inserted":
                                total_imported += 1
                            elif outcome == "updated":
                                total_updated += 1
                            else:
                                total_skipped += 1
                        except Exception as e:
                            total_failed += 1
                            failed_rowids.append(int(r["ROWID"]))
                            err = f"Message ROWID={r['ROWID']}: {type(e).__name__}: {e}"
                            errors.append(err)
                            if len(errors) <= 10:
                                logger.error("[imessage_import] %s", err)

                logger.debug(
                    "[imessage_import] Batch %d-%d : %d importes",
                    current,
                    batch_end,
                    total_imported,
                )

            except (sqlite3.Error, jarvis_sqlite.Error) as e:
                total_failed += 1
                failed_rowids.append(current)
                errors.append(f"Batch {current}-{batch_end}: {e}")
                logger.error(
                    "[imessage_import] Echec batch %d-%d : %s", current, batch_end, e
                )

            current = batch_end + 1

        return {
            "imported": total_imported,
            "updated": total_updated,
            "skipped": total_skipped,
            "failed": total_failed,
            "errors": errors,
            "failed_rowids": failed_rowids,
            "last_contiguous_rowid": min(failed_rowids) - 1
            if failed_rowids
            else to_rowid,
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
        attributed_body: bytes | memoryview | None = None,
    ) -> Literal["inserted", "updated", "skipped"]:
        """Insère, met à jour (date/texte) ou ignore un message Apple."""
        body_blob = bytes(attributed_body) if attributed_body else None
        occurred_at_utc = _apple_ts_to_iso(date)
        source_updated_at_utc = datetime.now(timezone.utc).isoformat()

        if (apple_handle_id in (None, 0)) and int(is_from_me):
            jarvis_handle_id = self._ensure_self_handle(conn, handles_map)
        else:
            jarvis_handle_id = (
                handles_map.get(apple_handle_id) if apple_handle_id else None
            )
        jarvis_chat_id = self._resolve_chat_id(
            conn,
            apple_chat_roomname,
            chats_map,
            apple_handle_id,
        )

        existing = conn.execute(
            """SELECT id, date, guid, is_from_me, text, attributed_body
               FROM imessage_messages WHERE apple_rowid = ?""",
            (apple_rowid,),
        ).fetchone()
        if existing:
            stored_text = existing["text"] or ""
            incoming = (text or "").strip()
            effective_text = text if incoming else stored_text
            content_hash = _compute_content_hash(
                date, apple_handle_id, effective_text, guid, apple_rowid
            )
            content_complete = bool((effective_text or "").strip())
            same = (
                int(existing["date"] or 0) == date
                and (existing["guid"] or "") == (guid or "")
                and int(existing["is_from_me"] or 0) == int(is_from_me)
                and stored_text == (effective_text or "")
            )
            if same:
                return "skipped"
            conn.execute(
                """UPDATE imessage_messages
                   SET guid = ?, chat_id = COALESCE(?, chat_id),
                       handle_id = COALESCE(?, handle_id),
                       text = ?, attributed_body = COALESCE(?, attributed_body),
                       date = ?, date_read = ?, is_from_me = ?, is_read = ?,
                       item_type = ?, group_title = ?,
                       associated_message_guid = ?, associated_message_type = ?,
                       content_hash = ?, occurred_at_utc = ?,
                       source_updated_at_utc = ?, content_complete = ?,
                       ingestion_completeness = ?
                   WHERE apple_rowid = ?""",
                (
                    guid,
                    jarvis_chat_id,
                    jarvis_handle_id,
                    effective_text,
                    body_blob,
                    date,
                    date_read,
                    is_from_me,
                    is_read,
                    item_type,
                    group_title,
                    associated_message_guid,
                    associated_message_type,
                    content_hash,
                    occurred_at_utc,
                    source_updated_at_utc,
                    int(content_complete),
                    "complete" if content_complete else "metadata",
                    apple_rowid,
                ),
            )
            return "updated"

        if guid:
            existing_guid = conn.execute(
                "SELECT id FROM imessage_messages WHERE guid = ?",
                (guid,),
            ).fetchone()
            if existing_guid:
                conn.execute(
                    """UPDATE imessage_messages
                       SET apple_rowid = ?, date = ?, occurred_at_utc = ?,
                           source_updated_at_utc = ?, date_read = ?, is_read = ?,
                           is_from_me = ?
                       WHERE guid = ? AND (apple_rowid IS NULL OR apple_rowid != ?)""",
                    (
                        apple_rowid,
                        date,
                        occurred_at_utc,
                        source_updated_at_utc,
                        date_read,
                        is_read,
                        is_from_me,
                        guid,
                        apple_rowid,
                    ),
                )
                return "updated"

        content_hash = _compute_content_hash(
            date, apple_handle_id, text, guid, apple_rowid
        )
        content_complete = bool((text or "").strip())

        try:
            conn.execute(
                """INSERT INTO imessage_messages
                   (apple_rowid, guid, chat_id, handle_id, text, attributed_body,
                    date, date_read, is_from_me, is_read, item_type, group_title,
                    associated_message_guid, associated_message_type, content_hash,
                    occurred_at_utc, source_updated_at_utc, content_complete,
                    ingestion_completeness)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    apple_rowid,
                    guid,
                    jarvis_chat_id,
                    jarvis_handle_id,
                    text,
                    body_blob,
                    date,
                    date_read,
                    is_from_me,
                    is_read,
                    item_type,
                    group_title,
                    associated_message_guid,
                    associated_message_type,
                    content_hash,
                    occurred_at_utc,
                    source_updated_at_utc,
                    int(content_complete),
                    "complete" if content_complete else "metadata",
                ),
            )
            return "inserted"
        except (sqlite3.IntegrityError, jarvis_sqlite.IntegrityError) as e:
            err_str = str(e).lower()
            if "unique" in err_str:
                return "skipped"
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
                except (sqlite3.IntegrityError, jarvis_sqlite.IntegrityError):
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
                    except (sqlite3.IntegrityError, jarvis_sqlite.IntegrityError):
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
                except (sqlite3.IntegrityError, jarvis_sqlite.IntegrityError):
                    pass
        return {"imported": imported, "linked": 0}

    # ── Import reactions ───────────────────────────────────────

    def _store_reactions(
        self,
        rows: list[Any],
        handles_map: dict[int, int],
    ) -> dict[str, int]:
        """Persiste les tapbacks 2000-2999, GUID normalisé, handle __self__ si from_me."""
        imported = 0
        with get_db() as jarvis_conn:
            for r in rows:
                assoc_guid = normalize_associated_message_guid(
                    r["associated_message_guid"]
                )
                if not assoc_guid:
                    continue
                reaction_type = int(r["associated_message_type"] or 0)
                if reaction_type < 2000 or reaction_type > 2999:
                    continue
                target = jarvis_conn.execute(
                    "SELECT id FROM imessage_messages WHERE guid = ?",
                    (assoc_guid,),
                ).fetchone()
                if not target:
                    continue
                apple_handle_id = r["handle_id"]
                if apple_handle_id in (None, 0) and int(r["is_from_me"] or 0):
                    reactor_jarvis_id = self._ensure_self_handle(
                        jarvis_conn, handles_map
                    )
                else:
                    reactor_jarvis_id = handles_map.get(apple_handle_id)
                if not reactor_jarvis_id:
                    continue
                try:
                    cur = jarvis_conn.execute(
                        """INSERT INTO imessage_reactions
                           (message_id, reactor_handle_id, reaction_type,
                            apple_associated_guid)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(message_id, reactor_handle_id) DO UPDATE SET
                               reaction_type = excluded.reaction_type,
                               apple_associated_guid = excluded.apple_associated_guid""",
                        (
                            target["id"],
                            reactor_jarvis_id,
                            reaction_type,
                            r["associated_message_guid"],
                        ),
                    )
                    if cur.rowcount > 0:
                        imported += 1
                except (sqlite3.IntegrityError, jarvis_sqlite.IntegrityError):
                    pass
        return {"imported": imported}

    def _import_reactions(
        self,
        chat_conn: sqlite3.Connection,
        handles_map: dict[int, int],
    ) -> dict[str, int]:
        """Importe les reactions (tapbacks iMessage).

        Dans chat.db, les reactions sont des messages avec associated_message_type
        dans la plage 2000-2999 (les tapbacks) ou des lignes dediees.
        """
        rows = chat_conn.execute(
            """SELECT ROWID, handle_id, associated_message_guid, associated_message_type, is_from_me
               FROM message
               WHERE associated_message_type > 0
                 AND associated_message_guid IS NOT NULL"""
        ).fetchall()
        return self._store_reactions(rows, handles_map)

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
        return self._store_reactions(rows, handles_map)

    # ── Backfill metadata ──────────────────────────────────────

    def backfill_orphan_messages(self) -> dict[str, int]:
        """Reassocie handle/chat/text pour les messages deja importes sans lien."""
        if not self.is_available():
            return {"updated": 0, "text_recovered": 0, "imported": 0}

        stats = {"updated": 0, "text_recovered": 0, "imported": 0}
        chat_conn = self._open_chat_db()
        try:
            handles_map = self._import_handles(chat_conn)
            chats_map = self._import_chats(chat_conn)
            self._import_chat_handles(chat_conn, handles_map, chats_map)

            with get_db() as jarvis_conn:
                orphan_rows = jarvis_conn.execute(
                    """SELECT id, apple_rowid, text, attributed_body
                       FROM imessage_messages
                       WHERE handle_id IS NULL OR chat_id IS NULL
                          OR TRIM(COALESCE(text, '')) = ''"""
                ).fetchall()

                for row in orphan_rows:
                    apple_row = chat_conn.execute(
                        _message_batch_sql(chat_conn),
                        (row["apple_rowid"], row["apple_rowid"]),
                    ).fetchone()
                    if not apple_row:
                        continue

                    text = (
                        message_text_from_row(
                            apple_row["text"],
                            apple_row["attributedBody"],
                        )
                        or (row["text"] or "").strip()
                        or None
                    )

                    resolved_handle = apple_row["resolved_handle_id"]
                    if resolved_handle in (None, 0):
                        if int(apple_row["is_from_me"] or 0):
                            jarvis_handle_id = self._ensure_self_handle(
                                jarvis_conn, handles_map
                            )
                        else:
                            jarvis_handle_id = None
                    else:
                        resolved_handle = int(resolved_handle)
                        jarvis_handle_id = handles_map.get(resolved_handle)
                    jarvis_chat_id = self._resolve_chat_id(
                        jarvis_conn,
                        str(apple_row["resolved_chat_identifier"] or ""),
                        chats_map,
                        resolved_handle,
                    )

                    updates: list[str] = []
                    params: list[Any] = []
                    if jarvis_handle_id:
                        updates.append("handle_id = ?")
                        params.append(jarvis_handle_id)
                    if jarvis_chat_id:
                        updates.append("chat_id = ?")
                        params.append(jarvis_chat_id)
                    if text and not (row["text"] or "").strip():
                        updates.append("text = ?")
                        params.append(text)
                        stats["text_recovered"] += 1
                    body_blob = apple_row["attributedBody"]
                    if body_blob and not row["attributed_body"]:
                        updates.append("attributed_body = ?")
                        params.append(bytes(body_blob))

                    if updates:
                        params.append(row["id"])
                        jarvis_conn.execute(
                            f"UPDATE imessage_messages SET {', '.join(updates)} WHERE id = ?",
                            params,
                        )
                        stats["updated"] += 1
        finally:
            self._close_chat_db()

        return stats

    def reconcile_deleted_messages(self) -> int:
        """Supprime localement uniquement après un inventaire complet de chat.db."""

        if not self.is_available():
            raise RuntimeError("chat.db inaccessible — reconciliation impossible")
        chat_conn = self._open_chat_db()
        try:
            source_rows = {
                int(row["ROWID"]): str(row["guid"] or "")
                for row in chat_conn.execute(
                    "SELECT ROWID, guid FROM message"
                ).fetchall()
            }
            handle_count = int(
                chat_conn.execute("SELECT COUNT(*) c FROM handle").fetchone()["c"]
            )
            chat_count = int(
                chat_conn.execute("SELECT COUNT(*) c FROM chat").fetchone()["c"]
            )
        finally:
            self._close_chat_db()

        with get_db() as jarvis_conn:
            cached = jarvis_conn.execute(
                "SELECT id, apple_rowid, guid FROM imessage_messages"
            ).fetchall()
            if not source_rows and cached and handle_count == 0 and chat_count == 0:
                raise RuntimeError(
                    "chat.db sans messages, handles ni chats alors que jarvis.db "
                    "contient des messages — réconciliation annulée pour éviter "
                    "une purge totale"
                )
            stale_ids = [
                int(row["id"])
                for row in cached
                if int(row["apple_rowid"]) not in source_rows
                or source_rows[int(row["apple_rowid"])] != str(row["guid"] or "")
            ]
            if not stale_ids:
                return 0
            placeholders = ",".join("?" for _ in stale_ids)
            jarvis_conn.execute(
                f"DELETE FROM imessage_reactions WHERE message_id IN ({placeholders})",  # noqa: S608
                stale_ids,
            )
            jarvis_conn.execute(
                f"DELETE FROM imessage_message_attachments WHERE message_id IN ({placeholders})",  # noqa: S608
                stale_ids,
            )
            jarvis_conn.execute(
                f"DELETE FROM imessage_messages WHERE id IN ({placeholders})",  # noqa: S608
                stale_ids,
            )
        return len(stale_ids)

    def _ensure_message_parity(
        self,
        chat_conn: sqlite3.Connection,
        handles_map: dict[int, int],
        chats_map: dict[int, int],
    ) -> dict[str, int]:
        """Importe les rowids importables absents et rafraîchit les dates périmées.

        Inventaire léger (ROWID + date seulement) — pas de re-décodage des 41k BLOB.
        Apple reste la source de vérité ; les extras jarvis sont laissés en place.
        """
        max_rowid = int(
            chat_conn.execute(
                "SELECT COALESCE(MAX(ROWID), 0) AS m FROM message"
            ).fetchone()["m"]
        )
        if max_rowid <= 0:
            return {"missing_imported": 0, "refreshed": 0, "still_missing": 0}

        parts = _chat_message_sql_parts(chat_conn)
        apple_rows = chat_conn.execute(
            f"""
            SELECT m.ROWID AS ROWID, m.date AS date
            FROM message m
            WHERE m.ROWID BETWEEN ? AND ?
              AND {parts["importable_predicate"]}
            ORDER BY m.ROWID ASC
            """,
            (0, max_rowid),
        ).fetchall()

        with get_db() as jarvis_conn:
            jarvis_index = {
                int(row["apple_rowid"]): int(row["date"] or 0)
                for row in jarvis_conn.execute(
                    "SELECT apple_rowid, date FROM imessage_messages "
                    "WHERE apple_rowid IS NOT NULL"
                )
            }

        missing_imported = 0
        refreshed = 0
        for apple_row in apple_rows:
            rid = int(apple_row["ROWID"])
            apple_date = int(apple_row["date"] or 0)
            stored_date = jarvis_index.get(rid)
            if stored_date is None:
                result = self._import_message_batch(
                    chat_conn,
                    handles_map,
                    chats_map,
                    from_rowid=rid,
                    to_rowid=rid,
                )
                missing_imported += int(result["imported"])
                if result["imported"] or result.get("updated"):
                    jarvis_index[rid] = apple_date
            elif stored_date != apple_date:
                result = self._import_message_batch(
                    chat_conn,
                    handles_map,
                    chats_map,
                    from_rowid=rid,
                    to_rowid=rid,
                )
                refreshed += int(result.get("updated", 0))
                if result.get("updated"):
                    jarvis_index[rid] = apple_date

        still_missing = sum(
            1 for row in apple_rows if int(row["ROWID"]) not in jarvis_index
        )
        return {
            "missing_imported": missing_imported,
            "refreshed": refreshed,
            "still_missing": still_missing,
        }

    # ── Reconciliation ─────────────────────────────────────────

    def reconcile(self) -> ReconciliationReport:
        """Répare la parité chat.db → jarvis.db, puis audite.

        ``ok`` uniquement si chaque rowid importable Apple est présent dans jarvis.
        Les extras jarvis ne font pas échouer le rapport.
        """
        report = ReconciliationReport()

        if not self.is_available():
            report.ok = False
            return report

        still_missing = 0
        chat_conn = self._open_chat_db()
        try:
            report.chat_db_messages = _count_importable_messages(chat_conn)
            report.chat_db_chats = chat_conn.execute(
                "SELECT COUNT(*) c FROM chat"
            ).fetchone()["c"]
            report.chat_db_handles = chat_conn.execute(
                "SELECT COUNT(*) c FROM handle"
            ).fetchone()["c"]

            handles_map = self._import_handles(chat_conn)
            chats_map = self._import_chats(chat_conn)
            self._import_chat_handles(chat_conn, handles_map, chats_map)

            parity = self._ensure_message_parity(chat_conn, handles_map, chats_map)
            report.missing_imported = parity["missing_imported"]
            report.refreshed = parity["refreshed"]
            still_missing = parity["still_missing"]

            reac = self._import_reactions(chat_conn, handles_map)
            report.reactions_imported = int(reac.get("imported", 0))
        finally:
            self._close_chat_db()

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

            report.orphan_messages = jarvis_conn.execute(
                """SELECT COUNT(*) c FROM imessage_messages m
                   WHERE m.chat_id IS NULL
                      OR m.handle_id IS NULL
                      OR m.chat_id NOT IN (SELECT id FROM imessage_chats)
                      OR m.handle_id NOT IN (SELECT id FROM imessage_handles)"""
            ).fetchone()["c"]

            if report.orphan_messages > 0:
                backfill_stats = self.backfill_orphan_messages()
                report.orphan_fixed = int(backfill_stats.get("updated", 0))
                report.jarvis_db_messages = jarvis_conn.execute(
                    "SELECT COUNT(*) c FROM imessage_messages"
                ).fetchone()["c"]
                report.orphan_messages = jarvis_conn.execute(
                    """SELECT COUNT(*) c FROM imessage_messages m
                       WHERE m.chat_id IS NULL OR m.handle_id IS NULL"""
                ).fetchone()["c"]

            report.duplicates_found = jarvis_conn.execute(
                """SELECT COUNT(*) c FROM (
                       SELECT guid, COUNT(*) cnt FROM imessage_messages
                       GROUP BY guid HAVING cnt > 1
                   )"""
            ).fetchone()["c"]
            if report.duplicates_found > 0:
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

        report.ok = still_missing == 0

        if report.ok:
            logger.info(
                "[imessage_import] Reconciliation OK — "
                "%d messages (chat.db=%d), manquants rattrapés=%d, dates=%d, "
                "tapbacks=%d, %d orphelins fixes, %d doublons supprimes",
                report.jarvis_db_messages,
                report.chat_db_messages,
                report.missing_imported,
                report.refreshed,
                report.reactions_imported,
                report.orphan_fixed,
                report.duplicates_removed,
            )
        else:
            logger.warning(
                "[imessage_import] Reconciliation : %d rowids importables encore absents — "
                "chat.db=%d messages, jarvis.db=%d messages",
                still_missing,
                report.chat_db_messages,
                report.jarvis_db_messages,
            )

        return report


imessage_importer = IMessageImporter()
from database import dbapi as jarvis_sqlite

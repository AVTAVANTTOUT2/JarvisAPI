"""Lecteur iMessage compatible, adossé à :mod:`integrations.apple_data`.

Le reader conserve l'API historique utilisée par les analyseurs relationnels
et les jobs périodiques. Toute lecture SQLite de Messages.app passe désormais
par ``AppleDataService``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .apple_data import (
    AppleDataService,
    apple_data,
    apple_epoch_to_datetime,
)
from .imessage_cursor import (
    advance_consumer_cursor,
    initialize_consumer_cursor,
)

logger = logging.getLogger(__name__)

# Alias rétrocompatibles : la conversion n'est implémentée que dans apple_data.py.
_apple_ts_to_datetime = apple_epoch_to_datetime
_apple_ts_to_datetime_from_value = apple_epoch_to_datetime


class IMessageReader:
    """API de lecture iMessage historique, sans ouverture directe de ``chat.db``."""

    def __init__(self, data_service: AppleDataService | None = None) -> None:
        self._apple_data = data_service or apple_data
        self._available: bool | None = None
        self.cursor_name = "reader.intelligence"

    @property
    def db_path(self) -> Path:
        """Chemin exposé pour compatibilité et injection dans les tests."""
        return self._apple_data.db_path

    @db_path.setter
    def db_path(self, value: str | Path) -> None:
        self._apple_data = self._apple_data.with_db_path(value)
        self._available = None

    def is_available(self) -> bool:
        """Vérifie une fois la disponibilité du service Apple local."""
        if self._available is not None:
            return self._available
        logger.info("[imessage_reader] Tentative accès chat.db : %s", self.db_path)
        logger.info("[imessage_reader] Fichier existe : %s", self.db_path.exists())
        self._available = self._apple_data.is_available()
        if self._available:
            logger.info("[iMsgReader] chat.db accessible en lecture")
        else:
            logger.warning(
                "[iMsgReader] chat.db inaccessible — Full Disk Access requis "
                "pour l'app qui lance JARVIS"
            )
        return self._available

    def get_all_contacts(self) -> list[dict]:
        """Contacts uniques avec nombre de messages et dernière date."""
        if not self.is_available():
            return []
        try:
            return self._apple_data.get_contacts()
        except Exception as exc:
            logger.error("[imessage_reader] get_all_contacts : %s", exc)
            return []

    def get_all_conversation_stats_full(self) -> list[dict]:
        """Retourne toutes les conversations distinctes avec leurs statistiques."""
        if not self.is_available():
            return []
        try:
            return self._apple_data.get_all_conversation_stats()
        except Exception as exc:
            logger.error("[iMsgReader] get_all_conversation_stats_full : %s", exc)
            return []

    def get_conversation(
        self,
        handle: str,
        limit: int = 100,
        since_rowid: int = 0,
    ) -> list[dict]:
        """Messages d'un contact depuis un ROWID donné."""
        if not self.is_available():
            return []
        try:
            return self._apple_data.get_conversation(
                handle,
                limit=limit,
                since_rowid=since_rowid,
            )
        except Exception as exc:
            logger.error("[iMsgReader] get_conversation(%s) : %s", handle, exc)
            return []

    def get_recent_conversation(self, handle: str, limit: int = 30) -> list[dict]:
        """Derniers messages avec ce handle, en ordre chronologique."""
        if not self.is_available():
            return []
        try:
            return self._apple_data.get_recent_conversation(handle, limit=limit)
        except Exception as exc:
            logger.error("[iMsgReader] get_recent_conversation(%s) : %s", handle, exc)
            return []

    def get_conversation_with(self, name_or_handle: str, limit: int = 50) -> list[dict]:
        """Cherche un handle par motif puis renvoie le fil récent."""
        if not self.is_available():
            return []
        try:
            return self._apple_data.get_conversation_with(name_or_handle, limit=limit)
        except Exception as exc:
            logger.error(
                "[iMsgReader] get_conversation_with(%s) : %s", name_or_handle, exc
            )
            return []

    def get_conversation_for_period(
        self,
        handle: str,
        days: int = 90,
        limit: int = 5000,
    ) -> list[dict]:
        """Messages récents filtrés par période, avec la forme historique."""
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
        """Recherche textuelle dans les messages iMessage."""
        if not self.is_available():
            return []
        try:
            return self._apple_data.search_messages(query, limit=limit)
        except Exception as exc:
            logger.error("[iMsgReader] search_messages : %s", exc)
            return []

    def scan_new_messages(self) -> int:
        """Retourne le nombre de nouveaux messages depuis le dernier scan."""
        count, _ = self.scan_new_messages_with_last_id()
        return count

    def scan_new_messages_with_last_id(self) -> tuple[int, int]:
        """Retourne ``(nombre de nouveaux messages, dernier ROWID)``.

        Compat historique : avance le curseur immédiatement. Le chemin H24
        (`periodic_scan`) utilise ``peek_new_messages`` + avance après succès.
        """
        if not self.is_available():
            return 0, 0
        try:
            current_max = self._apple_data.get_max_rowid()
            last_max = initialize_consumer_cursor(self.cursor_name, current_max)
            if current_max <= last_max:
                return 0, current_max
            count = current_max - last_max
            advance_consumer_cursor(self.cursor_name, current_max)
            return count, current_max
        except Exception as exc:
            logger.warning("[imessage_reader] scan_new_messages_with_last_id : %s", exc)
            return 0, 0

    def peek_new_messages(self, *, limit: int = 100) -> tuple[list[dict], int]:
        """Lit les nouveaux messages SANS avancer le curseur.

        Retourne ``(messages, since_rowid)``. Le curseur n'avance qu'après un
        traitement réussi — sinon les messages restent à retravailler.
        """
        if not self.is_available():
            return [], 0
        try:
            current_max = self._apple_data.get_max_rowid()
            last_max = initialize_consumer_cursor(self.cursor_name, current_max)
            if current_max <= last_max:
                return [], last_max
            messages = self._apple_data.get_new_messages(last_max, limit=limit)
            return list(messages), last_max
        except Exception as exc:
            logger.warning("[imessage_reader] peek_new_messages : %s", exc)
            return [], 0

    def sync_knowledge_mirror(self) -> dict[str, Any]:
        """Alimente ``imessage_messages`` (source de vérité du retrieval)."""
        try:
            from integrations.imessage_import import IMessageImporter

            importer = IMessageImporter()
            if not importer.is_available():
                return {"status": "unavailable"}
            result = importer.sync_incremental()
            if result.errors == ["sync_already_running"]:
                return {"status": "busy"}
            if result.errors:
                return {
                    "status": "error",
                    "imported": result.total_messages,
                    "errors": list(result.errors)[:5],
                }
            return {
                "status": "ok",
                "imported": int(result.total_messages or 0),
                "skipped": int(result.total_skipped or 0),
            }
        except Exception as exc:
            logger.warning("[imessage_reader] sync_knowledge_mirror : %s", exc)
            return {"status": "error", "error": type(exc).__name__}

    async def periodic_scan(self, interval: int = 300) -> None:
        """Boucle H24 : miroir connaissance + intelligence (curseur après succès)."""
        logger.info(
            "[imessage_reader] Scan périodique démarré (interval=%ds)", interval
        )
        while True:
            try:
                if self.is_available():
                    sync_stats = await asyncio.to_thread(self.sync_knowledge_mirror)
                    if sync_stats.get("status") == "ok" and sync_stats.get("imported"):
                        logger.info(
                            "[imessage_reader] Miroir iMessage +%s msg "
                            "(skip=%s)",
                            sync_stats.get("imported"),
                            sync_stats.get("skipped"),
                        )
                    elif sync_stats.get("status") not in {"ok", "busy", "unavailable"}:
                        logger.warning(
                            "[imessage_reader] Sync miroir : %s", sync_stats
                        )

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


async def _trigger_message_intelligence(
    since_rowid: int,
    messages: list[dict] | None = None,
) -> bool:
    """Analyse le lot Apple sans mélanger ses ROWID avec ``messages.id``.

    Retourne True seulement si l'analyse a abouti — le curseur peut alors avancer.
    """
    try:
        from jarvis.message_intelligence import analyze_message_batch

        batch = (
            list(messages)
            if messages is not None
            else apple_data.get_new_messages(since_rowid, limit=100)
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

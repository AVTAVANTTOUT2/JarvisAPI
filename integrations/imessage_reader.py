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
            logger.error("[iMsgReader] get_conversation_with(%s) : %s", name_or_handle, exc)
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
        """Retourne ``(nombre de nouveaux messages, dernier ROWID)``."""
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

    async def periodic_scan(self, interval: int = 300) -> None:
        """Boucle périodique de sourcing iMessage en lecture seule."""
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
                        asyncio.create_task(
                            _trigger_message_intelligence(since_id=last_id - count),
                            name="message_intelligence",
                        )
            except Exception as exc:
                logger.error(
                    "[imessage_reader] ÉCHEC scan — sourcing pourrait être bloqué : %s",
                    exc,
                    exc_info=True,
                )
            await asyncio.sleep(interval)


async def _trigger_message_intelligence(since_id: int) -> None:
    """Déclenche l'analyse asynchrone après un scan réussi."""
    try:
        from jarvis.message_intelligence import analyze_recent_messages

        result = await analyze_recent_messages(since_id=since_id)
        if result.get("status") != "ok":
            logger.debug(
                "[imessage_reader] message_intelligence terminé : %s",
                result.get("status"),
            )
    except Exception as exc:
        logger.warning("[imessage_reader] message_intelligence erreur : %s", exc)


imessage_reader = IMessageReader()


__all__ = [
    "IMessageReader",
    "_apple_ts_to_datetime",
    "_apple_ts_to_datetime_from_value",
    "imessage_reader",
]

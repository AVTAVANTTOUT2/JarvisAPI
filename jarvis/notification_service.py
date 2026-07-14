"""Service métier unique pour les notifications JARVIS.

Ce module centralise la politique de priorité, de déduplication et de
diffusion. La couche ``database`` conserve uniquement les primitives de
persistance et une façade rétrocompatible pour les anciens appelants.
"""

from __future__ import annotations

from typing import Any, Final

import database
from database import notifications as notification_store

from .event_bus import event_bus
from .events import NotificationCreated


VALID_NOTIFICATION_PRIORITIES: Final[frozenset[str]] = frozenset(
    {"urgent", "high", "medium", "low"}
)
DEFAULT_DEDUPLICATION_WINDOW_SECONDS: Final[int] = 300


class NotificationService:
    """Orchestre la création et la consultation des notifications.

    La déduplication porte sur ``source``, ``title`` et ``email_id`` dans une
    fenêtre courte. Elle évite les alertes répétées d'un même producteur sans
    supprimer des notifications liées à des emails distincts.
    """

    def create(
        self,
        source: str,
        title: str,
        content: str | None = None,
        priority: str = "medium",
        email_id: str | None = None,
        *,
        deduplication_window_seconds: int | None = DEFAULT_DEDUPLICATION_WINDOW_SECONDS,
    ) -> int:
        """Crée une notification, ou retourne celle déjà créée récemment.

        Les priorités ``urgent`` et ``high`` déclenchent un Web Push
        best-effort après le commit. ``notification.created`` n'est émis que
        lorsqu'une nouvelle ligne a réellement été persistée.
        """
        normalized_priority = self._normalize_priority(priority)
        notification_id, created = notification_store._insert_notification(
            source=source,
            title=title,
            content=content,
            priority=normalized_priority,
            email_id=email_id,
            deduplication_window_seconds=deduplication_window_seconds,
        )
        if not created:
            return notification_id

        if normalized_priority in {"urgent", "high"}:
            # La façade database est intentionnellement consultée à l'appel :
            # les tests et intégrations historiques peuvent remplacer ce hook.
            database._dispatch_push_notification(title, content, normalized_priority)

        event_bus.emit_nowait(
            NotificationCreated(
                notification_id,
                notification_source=source,
                priority=normalized_priority,
                title=title,
                content=content,
            )
        )
        return notification_id

    def get_unread(self, limit: int = 50) -> list[dict[str, Any]]:
        """Retourne les notifications non lues par priorité puis récence."""
        return notification_store.get_unread_notifications(limit)

    def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Retourne les notifications lues et non lues les plus récentes."""
        return notification_store.get_recent_notifications(limit)

    def mark_read(self, notification_id: int) -> bool:
        """Marque une notification lue et indique si elle existait."""
        return notification_store.mark_notification_read(notification_id)

    def mark_all_read(self) -> int:
        """Marque toutes les notifications non lues et retourne leur nombre."""
        return notification_store.mark_all_notifications_read()

    @staticmethod
    def _normalize_priority(priority: str) -> str:
        return priority if priority in VALID_NOTIFICATION_PRIORITIES else "medium"


notification_service = NotificationService()

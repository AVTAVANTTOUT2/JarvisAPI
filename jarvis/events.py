"""Événements de domaine typés activés pendant la Phase 3."""

from __future__ import annotations

from typing import Any, ClassVar

from .event_bus import DOMAIN_EVENT_TYPES, JarvisEvent


class NotificationCreated(JarvisEvent):
    """Une notification persistée doit être poussée aux interfaces."""

    EVENT_TYPE: ClassVar[str] = "notification.created"

    def __init__(
        self,
        notification_id: int,
        notification_source: str,
        priority: str,
        title: str,
        content: str | None = None,
    ) -> None:
        super().__init__(
            type=self.EVENT_TYPE,
            source="database.notifications",
            data={
                "notification_id": notification_id,
                "source": notification_source,
                "priority": priority,
                "title": title,
                "content": content,
            },
        )


class TaskCreated(JarvisEvent):
    """Une nouvelle tâche a été persistée."""

    EVENT_TYPE: ClassVar[str] = "task.created"

    def __init__(
        self,
        task_id: int,
        title: str,
        priority: str,
        due_date: str | None,
    ) -> None:
        super().__init__(
            type=self.EVENT_TYPE,
            source="database.tasks",
            data={
                "task_id": task_id,
                "title": title,
                "priority": priority,
                "due_date": due_date,
            },
        )


class TaskUpdated(JarvisEvent):
    """Une tâche existante a changé."""

    EVENT_TYPE: ClassVar[str] = "task.updated"

    def __init__(self, task_id: int, changes: dict[str, Any]) -> None:
        super().__init__(
            type=self.EVENT_TYPE,
            source="database.tasks",
            data={"task_id": task_id, "changes": changes},
        )


class ConversationUpdated(JarvisEvent):
    """Une conversation a été créée ou modifiée."""

    EVENT_TYPE: ClassVar[str] = "conversation.updated"

    def __init__(self, conversation_id: int, changes: dict[str, Any]) -> None:
        super().__init__(
            type=self.EVENT_TYPE,
            source="database.conversations",
            data={"conversation_id": conversation_id, "changes": changes},
        )


class MessageSent(JarvisEvent):
    """Un message a été ajouté à une conversation JARVIS."""

    EVENT_TYPE: ClassVar[str] = "message.sent"

    def __init__(
        self,
        conversation_id: int,
        message_id: int,
        role: str,
        content: str,
    ) -> None:
        super().__init__(
            type=self.EVENT_TYPE,
            source="database.conversations",
            data={
                "conversation_id": conversation_id,
                "message_id": message_id,
                "role": role,
                "content_preview": content[:160],
            },
        )


class MemoryUpdated(JarvisEvent):
    """Le contexte mémoire agrégé doit être rafraîchi."""

    EVENT_TYPE: ClassVar[str] = "memory.updated"

    def __init__(
        self,
        context_id: int,
        context_type: str,
        description: str,
    ) -> None:
        super().__init__(
            type=self.EVENT_TYPE,
            source="database.people",
            data={
                "context_id": context_id,
                "type": context_type,
                "description": description,
            },
        )


class PersonUpserted(JarvisEvent):
    """Une personne a été créée ou enrichie."""

    EVENT_TYPE: ClassVar[str] = "person.upserted"

    def __init__(self, person_id: int, name: str, changes: dict[str, Any]) -> None:
        super().__init__(
            type=self.EVENT_TYPE,
            source="database.people",
            data={"person_id": person_id, "name": name, "changes": changes},
        )


class EpisodeSaved(JarvisEvent):
    """Un épisode de mémoire a été sauvegardé."""

    EVENT_TYPE: ClassVar[str] = "episode.saved"

    def __init__(
        self,
        episode_id: int,
        summary: str,
        importance: int,
    ) -> None:
        super().__init__(
            type=self.EVENT_TYPE,
            source="database.episodes",
            data={
                "episode_id": episode_id,
                "summary": summary,
                "importance": importance,
            },
        )


class PatternDetected(JarvisEvent):
    """Un nouveau pattern comportemental a été détecté."""

    EVENT_TYPE: ClassVar[str] = "pattern.detected"

    def __init__(self, pattern_id: int, pattern_type: str, description: str) -> None:
        super().__init__(
            type=self.EVENT_TYPE,
            source="database.patterns",
            data={
                "pattern_id": pattern_id,
                "type": pattern_type,
                "description": description,
            },
        )


class FactAdded(JarvisEvent):
    """Un fait durable a été ajouté à la mémoire."""

    EVENT_TYPE: ClassVar[str] = "fact.added"

    def __init__(
        self,
        fact_id: int,
        category: str,
        content: str,
        confidence: str,
    ) -> None:
        super().__init__(
            type=self.EVENT_TYPE,
            source="database.facts",
            data={
                "fact_id": fact_id,
                "category": category,
                "content": content,
                "confidence": confidence,
            },
        )


DOMAIN_EVENT_CLASSES: tuple[type[JarvisEvent], ...] = (
    NotificationCreated,
    TaskCreated,
    TaskUpdated,
    ConversationUpdated,
    MessageSent,
    MemoryUpdated,
    PersonUpserted,
    EpisodeSaved,
    PatternDetected,
    FactAdded,
)

assert tuple(event.EVENT_TYPE for event in DOMAIN_EVENT_CLASSES) == DOMAIN_EVENT_TYPES

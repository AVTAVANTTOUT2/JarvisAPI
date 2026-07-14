"""Bus d'événements central — chaque action du système émet ici.

Le frontend SSE consomme ce flux pour afficher l'activité en temps réel
dans Mission Control.

Utilisation :
    from jarvis.event_bus import event_bus, JarvisEvent
    await event_bus.emit(JarvisEvent(type="agent.start", agent="info", data={"model": "deepseek-v4-pro"}))

Architecture :
    - Singleton EventBus avec pattern pub/sub via asyncio.Queue
    - Historique glissant des 200 derniers événements
    - Chaque abonné reçoit une copie de chaque événement
    - Les abonnés morts (QueueFull répété) sont nettoyés automatiquement
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json as _json
import logging
import time as _time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from concurrent.futures import Future as ConcurrentFuture
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, Optional

logger = logging.getLogger("jarvis.event_bus")

# ── Constantes ─────────────────────────────────────────────────────────────────

MAX_HISTORY = 200
QUEUE_MAXSIZE = 200

# ── Types d'événements ────────────────────────────────────────────────────────

EVENT_TYPES: tuple[str, ...] = (
    # Pipeline vocal
    "voice.listening",
    "voice.speech_start",
    "voice.speech_end",
    "voice.stt_result",
    "voice.stt_error",
    # Orchestrateur
    "orchestrator.classify",
    "orchestrator.route",
    # Agents
    "agent.start",
    "agent.thinking",
    "agent.action",
    "agent.action_result",
    "agent.response",
    "agent.error",
    # TTS
    "tts.start",
    "tts.playing",
    "tts.done",
    # Workflow (réservé pour usage futur)
    "workflow.step_start",
    "workflow.step_done",
    "workflow.step_error",
    "workflow.complete",
    # Système
    "system.service_up",
    "system.service_down",
    "system.error",
    # Domaine applicatif — Phase 3
    "notification.created",
    "task.created",
    "task.updated",
    "conversation.updated",
    "message.sent",
    "memory.updated",
    "person.upserted",
    "episode.saved",
    "pattern.detected",
    "fact.added",
)

VALID_EVENT_TYPES: frozenset[str] = frozenset(EVENT_TYPES)
DOMAIN_EVENT_TYPES: tuple[str, ...] = EVENT_TYPES[-10:]


@dataclass(frozen=True)
class JarvisEvent:
    """Un événement émis par un composant JARVIS.

    Attributes:
        type: Type d'événement (doit être dans EVENT_TYPES)
        agent: Nom de l'agent émetteur (optionnel)
        data: Données associées (optionnel)
        timestamp: Horodatage Unix (float, secondes depuis epoch)
        event_id: Identifiant UUID unique pour l'idempotence.
        version: Version du schéma de payload.
        source: Module ou composant émetteur.
        checksum: SHA256 canonique du payload.
    """

    EVENT_TYPE: ClassVar[str | None] = None

    type: str
    agent: Optional[str] = None
    data: Optional[Mapping[str, Any]] = None
    timestamp: float = field(default_factory=_time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    version: int = 1
    source: Optional[str] = None
    checksum: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.type, str) or self.type not in VALID_EVENT_TYPES:
            logger.warning(
                "EventBus: type d'événement inconnu '%s' — sera ignoré par les clients",
                self.type,
            )
        if self.data is not None and not isinstance(self.data, Mapping):
            normalised_data: dict[str, Any] = {"value": str(self.data)}
        else:
            normalised_data = dict(self.data or {})
        object.__setattr__(
            self,
            "data",
            MappingProxyType(normalised_data) if self.data is not None else None,
        )
        object.__setattr__(self, "source", self.source or self.agent or "unknown")
        canonical_payload = _json.dumps(
            normalised_data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        object.__setattr__(
            self,
            "checksum",
            hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest(),
        )

    @property
    def event_type(self) -> str:
        """Nom canonique de l'événement (alias compatible de ``type``)."""
        return self.type

    @property
    def payload(self) -> dict[str, Any]:
        """Copie sérialisable du payload canonique."""
        return dict(self.data or {})

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le contrat canonique et ses alias historiques."""
        payload = self.payload
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "type": self.type,
            "version": self.version,
            "timestamp": self.timestamp,
            "source": self.source,
            "payload": payload,
            "data": payload if self.data is not None else None,
            "agent": self.agent,
            "checksum": self.checksum,
        }

    def to_sse(self) -> str:
        """Sérialise l'événement au format SSE (Server-Sent Events).

        Retourne une chaîne prête à être envoyée dans un flux HTTP SSE.
        """
        payload = self.to_dict()
        return f"data: {_json.dumps(payload, ensure_ascii=False)}\n\n"


EventHandler = Callable[[JarvisEvent], Awaitable[None] | None]
EventSelector = str | type[JarvisEvent] | Iterable[str | type[JarvisEvent]]


class EventBus:
    """Bus d'événements central — singleton.

    Pattern pub/sub : chaque abonné reçoit une asyncio.Queue alimentée
    par ``emit()``. L'historique glissant permet de rattraper les derniers
    événements lors d'une nouvelle connexion SSE.

    Les émissions synchrones et inter-threads sont rapatriées sur la boucle
    liée par ``bind_loop()`` quand l'application est active.
    """

    __slots__ = (
        "_subscribers",
        "_history",
        "_max_history",
        "_handlers",
        "_loop",
        "_pending",
    )

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[JarvisEvent]] = []
        self._history: list[JarvisEvent] = []
        self._max_history: int = MAX_HISTORY
        self._handlers: dict[str, list[EventHandler]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: set[asyncio.Future[None] | ConcurrentFuture[None]] = set()

    @staticmethod
    def _normalise_selectors(selector: EventSelector) -> tuple[str, ...]:
        if isinstance(selector, str) or (
            isinstance(selector, type) and issubclass(selector, JarvisEvent)
        ):
            values: Iterable[str | type[JarvisEvent]] = (selector,)
        else:
            values = selector

        event_types: list[str] = []
        for value in values:
            if isinstance(value, str):
                event_type = value
            else:
                event_type = getattr(value, "EVENT_TYPE", None)
                if not event_type:
                    raise ValueError(f"Classe d'événement sans EVENT_TYPE: {value!r}")
            if event_type != "*" and event_type not in VALID_EVENT_TYPES:
                raise ValueError(f"Type d'événement inconnu: {event_type}")
            if event_type not in event_types:
                event_types.append(event_type)
        return tuple(event_types)

    def on(self, selector: EventSelector) -> Callable[[EventHandler], EventHandler]:
        """Enregistre un handler pour un ou plusieurs types d'événements."""
        event_types = self._normalise_selectors(selector)

        def decorator(handler: EventHandler) -> EventHandler:
            for event_type in event_types:
                handlers = self._handlers.setdefault(event_type, [])
                if handler not in handlers:
                    handlers.append(handler)
            return handler

        return decorator

    def off(self, selector: EventSelector, handler: EventHandler) -> None:
        """Désenregistre un handler ; sans effet s'il est déjà absent."""
        for event_type in self._normalise_selectors(selector):
            handlers = self._handlers.get(event_type)
            if not handlers:
                continue
            self._handlers[event_type] = [item for item in handlers if item is not handler]

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Lie le bus à la boucle principale pour les émissions inter-threads."""
        self._loop = loop

    def unbind_loop(self) -> None:
        """Oublie la boucle principale lors de l'arrêt applicatif."""
        self._loop = None

    def subscribe(self) -> asyncio.Queue[JarvisEvent]:
        """Crée un nouvel abonnement et retourne la queue associée.

        L'abonné doit consommer les événements depuis cette queue.
        """
        q: asyncio.Queue[JarvisEvent] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._subscribers.append(q)
        logger.debug("EventBus: nouvel abonné — total=%d", len(self._subscribers))
        return q

    def unsubscribe(self, q: asyncio.Queue[JarvisEvent]) -> None:
        """Retire un abonnement.

        Sans effet si la queue n'est pas abonnée.
        """
        try:
            self._subscribers.remove(q)
            logger.debug("EventBus: abonné retiré — total=%d", len(self._subscribers))
        except ValueError:
            pass

    async def emit(self, event: JarvisEvent) -> None:
        """Émet un événement à tous les abonnés.

        L'événement est d'abord ajouté à l'historique (max MAX_HISTORY),
        puis distribué à chaque abonné. Les abonnés dont la queue est pleine
        sont retirés automatiquement.
        """
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        dead: list[asyncio.Queue[JarvisEvent]] = []
        for q in tuple(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
                logger.warning(
                    "EventBus: abonné lent (QueueFull) — retiré. "
                    "type=%s agent=%s",
                    event.type,
                    event.agent or "?",
                )

        for q in dead:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

        handlers: list[EventHandler] = []
        for handler in (
            *self._handlers.get(event.type, ()),
            *self._handlers.get("*", ()),
        ):
            if handler not in handlers:
                handlers.append(handler)
        if handlers:
            await asyncio.gather(
                *(self._invoke_handler(handler, event) for handler in handlers)
            )

    async def _invoke_handler(self, handler: EventHandler, event: JarvisEvent) -> None:
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception(
                "EventBus: handler %s en échec pour %s",
                getattr(handler, "__qualname__", repr(handler)),
                event.type,
            )

    def _track(
        self,
        future: asyncio.Future[None] | ConcurrentFuture[None],
    ) -> asyncio.Future[None] | ConcurrentFuture[None]:
        self._pending.add(future)
        future.add_done_callback(self._pending.discard)
        return future

    def emit_nowait(
        self,
        event: JarvisEvent,
    ) -> asyncio.Future[None] | ConcurrentFuture[None] | None:
        """Émet depuis du code synchrone, async ou un thread de scheduler."""
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if self._loop is not None and self._loop.is_running() and self._loop is not running_loop:
            return self._track(asyncio.run_coroutine_threadsafe(self.emit(event), self._loop))
        if running_loop is not None:
            return self._track(running_loop.create_task(self.emit(event)))

        asyncio.run(self.emit(event))
        return None

    async def wait_until_idle(self) -> None:
        """Attend les émissions fire-and-forget connues, utile au shutdown et aux tests."""
        while self._pending:
            pending = tuple(self._pending)
            awaitables: list[Awaitable[Any]] = []
            current_loop = asyncio.get_running_loop()
            for future in pending:
                if isinstance(future, asyncio.Future):
                    if future.get_loop() is current_loop:
                        awaitables.append(asyncio.shield(future))
                else:
                    awaitables.append(asyncio.wrap_future(future))
            if not awaitables:
                return
            await asyncio.gather(*awaitables, return_exceptions=True)

    def get_history(self, last_n: int = 50) -> list[dict]:
        """Retourne les N derniers événements sous forme de dicts.

        Args:
            last_n: Nombre d'événements à retourner (défaut 50).

        Returns:
            Liste de dicts, du plus ancien au plus récent.
        """
        return [event.to_dict() for event in self._history[-last_n:]]


# ── Singleton global ──────────────────────────────────────────────────────────

event_bus = EventBus()

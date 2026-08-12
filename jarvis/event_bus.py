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


def _current_event_profile_id() -> str:
    try:
        from database.core import current_profile_id

        return current_profile_id()
    except (ImportError, RuntimeError):
        return "default"

# ── Constantes ─────────────────────────────────────────────────────────────────

MAX_HISTORY = 200
QUEUE_MAXSIZE = 200
HANDLER_QUEUE_MAXSIZE = 200

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
    # Runtime agentique générique. Ce bloc reste avant les dix événements de
    # domaine historiques afin de préserver DOMAIN_EVENT_TYPES[-10:].
    "agent.run.created",
    "agent.run.classified",
    "agent.run.queued",
    "agent.run.provisioning",
    "agent.run.started",
    "agent.run.phase_changed",
    "agent.run.awaiting_approval",
    "agent.run.paused",
    "agent.run.resumed",
    "agent.run.blocked",
    "agent.run.verifying",
    "agent.run.reviewing",
    "agent.run.cancelling",
    "agent.run.cancelled",
    "agent.run.completed",
    "agent.run.failed",
    "agent.run.expired",
    "agent.run.provider_unavailable",
    "agent.tool.started",
    "agent.tool.completed",
    "agent.tool.failed",
    "agent.approval.requested",
    "agent.approval.resolved",
    "agent.artifact.created",
    # Commande de repas — suivi de livraison poussé vers l'interface.
    # Volontairement placé avant le bloc « Domaine applicatif » : ce dernier
    # est défini comme les dix derniers types et doit rester aligné sur les
    # dix classes typées de jarvis/events.py.
    "food.order_updated",
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
AGENTIC_EVENT_TYPES: tuple[str, ...] = tuple(
    event_type
    for event_type in EVENT_TYPES
    if event_type.startswith(("agent.run.", "agent.tool.", "agent.approval.", "agent.artifact."))
)


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
    profile_id: str = field(default_factory=_current_event_profile_id, init=False)
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
            "profile_id": self.profile_id,
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
        "_subscriber_profiles",
        "_history",
        "_max_history",
        "_handlers",
        "_handler_queues",
        "_handler_tasks",
        "_handler_queue_size",
        "_handler_loop",
        "_loop",
        "_pending",
    )

    def __init__(self, *, handler_queue_size: int = HANDLER_QUEUE_MAXSIZE) -> None:
        if handler_queue_size < 1:
            raise ValueError("handler_queue_size doit être positif")
        self._subscribers: list[asyncio.Queue[JarvisEvent]] = []
        self._subscriber_profiles: dict[asyncio.Queue[JarvisEvent], str] = {}
        self._history: list[JarvisEvent] = []
        self._max_history: int = MAX_HISTORY
        self._handlers: dict[str, list[EventHandler]] = {}
        self._handler_queues: dict[EventHandler, asyncio.Queue[JarvisEvent]] = {}
        self._handler_tasks: dict[EventHandler, asyncio.Task[None]] = {}
        self._handler_queue_size = handler_queue_size
        self._handler_loop: asyncio.AbstractEventLoop | None = None
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

    def _activate_handler_loop(
        self,
        current_loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Prépare les files pour la boucle courante sans réutilisation invalide."""
        if self._handler_loop is current_loop:
            return

        queued = sum(queue.qsize() for queue in self._handler_queues.values())
        active = sum(not task.done() for task in self._handler_tasks.values())
        unfinished = queued + active
        previous_loop_closed = (
            self._handler_loop is None or self._handler_loop.is_closed()
        )
        if unfinished and not previous_loop_closed:
            raise RuntimeError(
                "EventBus réutilisé sur une autre boucle avec des handlers en attente"
            )
        if unfinished:
            # Une boucle fermée a déjà annulé ses workers : ces éléments ne
            # sont plus récupérables. Les anciennes queues ne doivent surtout
            # pas bloquer le drainage de la nouvelle boucle.
            logger.warning(
                "EventBus: %d événement(s) abandonné(s) après fermeture de "
                "leur boucle asyncio",
                unfinished,
            )
        self._handler_queues.clear()
        self._handler_tasks.clear()
        self._handler_loop = current_loop

    def subscribe(self) -> asyncio.Queue[JarvisEvent]:
        """Crée un nouvel abonnement et retourne la queue associée.

        L'abonné doit consommer les événements depuis cette queue.
        """
        q: asyncio.Queue[JarvisEvent] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._subscribers.append(q)
        self._subscriber_profiles[q] = _current_event_profile_id()
        logger.debug("EventBus: nouvel abonné — total=%d", len(self._subscribers))
        return q

    def unsubscribe(self, q: asyncio.Queue[JarvisEvent]) -> None:
        """Retire un abonnement.

        Sans effet si la queue n'est pas abonnée.
        """
        try:
            self._subscribers.remove(q)
            self._subscriber_profiles.pop(q, None)
            logger.debug("EventBus: abonné retiré — total=%d", len(self._subscribers))
        except ValueError:
            pass

    async def emit(self, event: JarvisEvent) -> None:
        await self._emit(event, offload_sync_handlers=True)

    async def _emit(
        self,
        event: JarvisEvent,
        *,
        offload_sync_handlers: bool,
    ) -> None:
        """Émet un événement à tous les abonnés.

        L'événement est d'abord ajouté à l'historique (max MAX_HISTORY),
        puis distribué à chaque abonné. Les abonnés dont la queue est pleine
        sont retirés automatiquement.
        """
        current_loop = asyncio.get_running_loop()
        self._activate_handler_loop(current_loop)

        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        dead: list[asyncio.Queue[JarvisEvent]] = []
        for q in tuple(self._subscribers):
            if self._subscriber_profiles.get(q, "default") != event.profile_id:
                continue
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
                self._subscriber_profiles.pop(q, None)
            except ValueError:
                pass

        handlers: list[EventHandler] = []
        for handler in (
            *self._handlers.get(event.type, ()),
            *self._handlers.get("*", ()),
        ):
            if handler not in handlers:
                handlers.append(handler)
        for handler in handlers:
            queue = self._handler_queues.setdefault(
                handler,
                asyncio.Queue(maxsize=self._handler_queue_size),
            )
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.error(
                    "EventBus: file handler pleine — événement abandonné pour ce "
                    "consommateur. handler=%s type=%s capacité=%d",
                    getattr(handler, "__qualname__", repr(handler)),
                    event.type,
                    self._handler_queue_size,
                )
                continue
            task = self._handler_tasks.get(handler)
            if task is None or task.done():
                name = getattr(handler, "__qualname__", "anonymous")
                self._handler_tasks[handler] = asyncio.create_task(
                    self._drain_handler(
                        handler,
                        queue,
                        offload_sync_handlers=offload_sync_handlers,
                    ),
                    name=f"event_bus:{name}",
                )

    async def _drain_handler(
        self,
        handler: EventHandler,
        queue: asyncio.Queue[JarvisEvent],
        *,
        offload_sync_handlers: bool,
    ) -> None:
        """Vide séquentiellement la file privée d'un consommateur.

        Les consommateurs sont indépendants : une socket ou une synthèse lente
        ne retarde ni les autres handlers, ni la coroutine émettrice. Le worker
        s'arrête dès que sa file est vide et sera recréé à la prochaine émission.
        """
        current_task = asyncio.current_task()
        try:
            while True:
                try:
                    event = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    from database.core import use_profile

                    with use_profile(event.profile_id):
                        await self._invoke_handler(
                            handler,
                            event,
                            offload_sync_handler=offload_sync_handlers,
                        )
                finally:
                    queue.task_done()
        finally:
            if self._handler_tasks.get(handler) is current_task:
                self._handler_tasks.pop(handler, None)

    async def _invoke_handler(
        self,
        handler: EventHandler,
        event: JarvisEvent,
        *,
        offload_sync_handler: bool,
    ) -> None:
        try:
            if inspect.iscoroutinefunction(handler):
                result = handler(event)
            elif offload_sync_handler:
                # Une vraie boucle applicative ne doit jamais exécuter SQLite
                # ou un autre callback bloquant sur son thread principal.
                result = await asyncio.to_thread(handler, event)
            else:
                # Sans boucle persistante liée, emit_nowait() crée une boucle
                # temporaire et la draine avant de rendre la main. Déporter le
                # callback n'apporterait aucune concurrence utile.
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

        async def emit_and_drain() -> None:
            await self._emit(event, offload_sync_handlers=False)
            await self.wait_until_idle()

        # Sans boucle applicative liée, il n'existe aucun worker durable auquel
        # déléguer : on crée une boucle temporaire et on la vide avant fermeture.
        asyncio.run(emit_and_drain())
        return None

    async def wait_until_idle(self) -> None:
        """Attend émissions et files consommateurs, utile au shutdown et aux tests."""
        self._activate_handler_loop(asyncio.get_running_loop())
        while True:
            pending = tuple(self._pending)
            awaitables: list[Awaitable[Any]] = []
            current_loop = asyncio.get_running_loop()
            for future in pending:
                if isinstance(future, asyncio.Future):
                    if future.get_loop() is current_loop:
                        awaitables.append(asyncio.shield(future))
                else:
                    awaitables.append(asyncio.wrap_future(future))
            if awaitables:
                await asyncio.gather(*awaitables, return_exceptions=True)

            queues = tuple(self._handler_queues.values())
            if queues:
                await asyncio.gather(*(queue.join() for queue in queues))

            if not self._pending and all(queue.empty() for queue in queues):
                return

    def get_history(self, last_n: int = 50) -> list[dict]:
        """Retourne les N derniers événements sous forme de dicts.

        Args:
            last_n: Nombre d'événements à retourner (défaut 50).

        Returns:
            Liste de dicts, du plus ancien au plus récent.
        """
        profile_id = _current_event_profile_id()
        events = [event for event in self._history if event.profile_id == profile_id]
        return [event.to_dict() for event in events[-last_n:]]


# ── Singleton global ──────────────────────────────────────────────────────────

event_bus = EventBus()

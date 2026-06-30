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
import json as _json
import logging
import time as _time
from dataclasses import asdict, dataclass, field
from typing import Optional

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
)

VALID_EVENT_TYPES: frozenset[str] = frozenset(EVENT_TYPES)


@dataclass
class JarvisEvent:
    """Un événement émis par un composant JARVIS.

    Attributes:
        type: Type d'événement (doit être dans EVENT_TYPES)
        agent: Nom de l'agent émetteur (optionnel)
        data: Données associées (optionnel)
        timestamp: Horodatage Unix (float, secondes depuis epoch)
    """

    type: str
    agent: Optional[str] = None
    data: Optional[dict] = None
    timestamp: float = field(default_factory=_time.time)

    def __post_init__(self) -> None:
        if not isinstance(self.type, str) or self.type not in VALID_EVENT_TYPES:
            logger.warning(
                "EventBus: type d'événement inconnu '%s' — sera ignoré par les clients",
                self.type,
            )
        if self.data is not None and not isinstance(self.data, dict):
            self.data = {"value": str(self.data)}

    def to_sse(self) -> str:
        """Sérialise l'événement au format SSE (Server-Sent Events).

        Retourne une chaîne prête à être envoyée dans un flux HTTP SSE.
        """
        payload = asdict(self)
        return f"data: {_json.dumps(payload, ensure_ascii=False)}\n\n"


class EventBus:
    """Bus d'événements central — singleton.

    Pattern pub/sub : chaque abonné reçoit une asyncio.Queue alimentée
    par ``emit()``. L'historique glissant permet de rattraper les derniers
    événements lors d'une nouvelle connexion SSE.

    Thread-safe : conçu pour être appelé depuis la boucle asyncio uniquement.
    """

    __slots__ = ("_subscribers", "_history", "_max_history")

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[JarvisEvent]] = []
        self._history: list[JarvisEvent] = []
        self._max_history: int = MAX_HISTORY

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
        for q in self._subscribers:
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

    def get_history(self, last_n: int = 50) -> list[dict]:
        """Retourne les N derniers événements sous forme de dicts.

        Args:
            last_n: Nombre d'événements à retourner (défaut 50).

        Returns:
            Liste de dicts, du plus ancien au plus récent.
        """
        return [asdict(e) for e in self._history[-last_n:]]


# ── Singleton global ──────────────────────────────────────────────────────────

event_bus = EventBus()

"""Canal d'événements TV — schéma stable, allowlist stricte, diffusion bornée.

Ce module ne connaît ni FastAPI ni WebSocket : il normalise les événements
destinés à l'écran mural et les distribue à des abonnés dont la file est
bornée. Le transport vit dans `api/ws_tv.py`.

Trois propriétés sont tenues ici plutôt que dans le transport, pour qu'un
second consommateur (relais SSE, pont domotique) hérite des mêmes garanties
sans les réécrire :

- **allowlist de types** : seuls les types `tv.*` déclarés existent, et seul un
  sous-ensemble explicite du bus applicatif y est traduit. Les conversations et
  les messages n'ont aucune traduction — l'absence est vérifiée par un test
  statique, pas par une convention de rangement.
- **allowlist de champs** : chaque type déclare les clés de payload qu'il peut
  porter. Tout le reste est jeté avant même la redaction, donc un producteur qui
  enrichit son événement n'élargit pas mécaniquement ce qui s'affiche sur un
  écran visible depuis le salon.
- **backpressure explicite** : une file pleine perd son événement le plus ancien
  et incrémente un compteur. Au-delà d'un budget, l'abonné est déclaré en
  débordement et le transport le déconnecte, plutôt que de laisser un client
  lent retenir la mémoire du serveur.

La diffusion est unidirectionnelle par construction : aucune fonction de ce
module ne consomme d'entrée client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import config
from jarvis.event_bus import JarvisEvent, event_bus
from jarvis.security.redaction import redact_sensitive_mapping

logger = logging.getLogger("jarvis.tv_events")

# ── Contrat public ────────────────────────────────────────────────────────────

#: Version du schéma sérialisé. Toute suppression ou renommage de champ de
#: premier niveau doit l'incrémenter : la TV lit ce nombre pour refuser un
#: format qu'elle ne sait pas afficher.
TV_EVENT_SCHEMA_VERSION: Final[int] = 1

TV_VOICE_STATE: Final[str] = "tv.voice_state"
TV_NOTIFICATION: Final[str] = "tv.notification"
TV_TASK: Final[str] = "tv.task"
TV_SYSTEM: Final[str] = "tv.system"
TV_HEARTBEAT: Final[str] = "tv.heartbeat"

TV_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {TV_VOICE_STATE, TV_NOTIFICATION, TV_TASK, TV_SYSTEM, TV_HEARTBEAT}
)

#: Champs de premier niveau du contrat sérialisé, dans l'ordre documenté.
TV_EVENT_FIELDS: Final[tuple[str, ...]] = (
    "schema_version",
    "event_id",
    "type",
    "timestamp",
    "device_id",
    "state",
    "source",
    "payload",
)

#: Clés de payload autorisées par type. Une clé absente d'ici ne quitte jamais
#: le serveur, quelle que soit la générosité du producteur.
TV_PAYLOAD_FIELDS: Final[dict[str, frozenset[str]]] = {
    TV_VOICE_STATE: frozenset(
        {
            "enabled",
            "wake_word_enabled",
            "continuous_mode",
            "last_interaction",
            "error",
            "user_text",
            "jarvis_text",
        }
    ),
    TV_NOTIFICATION: frozenset(
        {"notification_id", "priority", "notification_source", "title"}
    ),
    TV_TASK: frozenset(
        {"task_id", "title", "priority", "status", "due_date", "category"}
    ),
    TV_SYSTEM: frozenset({"service", "detail"}),
    TV_HEARTBEAT: frozenset(),
}

#: Texte prononcé ou transcrit. C'est du contenu de conversation : il reste
#: hors du canal tant que l'opérateur ne l'autorise pas explicitement.
TV_TRANSCRIPT_FIELDS: Final[frozenset[str]] = frozenset({"user_text", "jarvis_text"})

#: Traductions autorisées depuis le bus applicatif. `message.sent`,
#: `conversation.updated`, `episode.saved`, `fact.added`, `memory.updated`,
#: `person.upserted` et `pattern.detected` sont volontairement absents : la TV
#: n'a aucune raison de recevoir le contenu d'une conversation ou d'une mémoire.
TV_BUS_EVENT_TRANSLATIONS: Final[dict[str, str]] = {
    "notification.created": TV_NOTIFICATION,
    "task.created": TV_TASK,
    "task.updated": TV_TASK,
    "system.service_up": TV_SYSTEM,
    "system.service_down": TV_SYSTEM,
    "system.error": TV_SYSTEM,
}

#: Préfixe des messages du daemon audio relayés vers l'état vocal TV.
AUDIO_DAEMON_EVENT_PREFIX: Final[str] = "audio_daemon"

_SLUG_PATTERN: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9_.:\-]+")
_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_.:\-]{1,64}")
#: Clés de configuration déjà signalées comme invalides — une seule alerte.
_INVALID_DEVICE_ID_KEYS: Final[set[str]] = set()
_UNKNOWN: Final[str] = "unknown"
_DEFAULT_DEVICE_ID: Final[str] = "mac_mini"
_MAX_DEVICE_ID_CHARS: Final[int] = 64
_MAX_STATE_CHARS: Final[int] = 32
_MAX_SOURCE_CHARS: Final[int] = 64
_MAX_PAYLOAD_DEPTH: Final[int] = 2
_MAX_SEQUENCE_ITEMS: Final[int] = 10
_MAX_MAPPING_KEYS: Final[int] = 10


class TvEventError(ValueError):
    """Événement TV refusé à la construction — type ou champ hors contrat."""


# ── Réglages (relus à chaque appel : un test ou l'opérateur peut les changer) ──


def _positive_int(name: str, default: int, *, minimum: int = 1) -> int:
    """Lit un entier de configuration en refusant les valeurs absurdes."""
    try:
        value = int(getattr(config, name, default))
    except (TypeError, ValueError):
        logger.warning("[tv-events] %s illisible — repli sur %d", name, default)
        return default
    return max(value, minimum)


def _flag(name: str, default: bool) -> bool:
    """Lit un interrupteur de configuration."""
    return bool(getattr(config, name, default))


def tv_events_enabled() -> bool:
    """Interrupteur global du canal TV."""
    return _flag("TV_EVENTS_ENABLED", True)


def transcripts_allowed() -> bool:
    """Autorisation explicite d'afficher du texte de conversation sur la TV."""
    return _flag("TV_EVENTS_INCLUDE_TRANSCRIPTS", False)


def max_text_chars() -> int:
    """Longueur maximale d'une chaîne de payload."""
    return _positive_int("TV_EVENT_MAX_TEXT_CHARS", 200, minimum=16)


def max_event_bytes() -> int:
    """Taille maximale d'un événement sérialisé, en octets."""
    return _positive_int("TV_WS_MAX_EVENT_BYTES", 8192, minimum=256)


def queue_maxsize() -> int:
    """Profondeur de la file par abonné."""
    return _positive_int("TV_WS_QUEUE_MAXSIZE", 100, minimum=1)


def max_dropped_events() -> int:
    """Budget de pertes toléré avant de déclarer un abonné en débordement."""
    return _positive_int("TV_WS_MAX_DROPPED_EVENTS", 200, minimum=1)


def default_device_id() -> str:
    """Machine à laquelle rattacher un événement sans device explicite.

    Une valeur qui n'est pas un identifiant est ignorée plutôt que nettoyée :
    `python-dotenv` retient le commentaire de fin de ligne comme valeur quand
    la clé est vide (`DEVICE_ID=   # vide = hostname`), et une phrase n'a rien
    à faire dans un champ de routage affiché à l'écran.
    """
    for name in ("TV_EVENTS_DEVICE_ID", "DEVICE_ID"):
        candidate = str(getattr(config, name, "") or "").strip()
        if not candidate:
            continue
        if _IDENTIFIER_PATTERN.fullmatch(candidate):
            return candidate
        if name not in _INVALID_DEVICE_ID_KEYS:
            _INVALID_DEVICE_ID_KEYS.add(name)
            logger.warning(
                "[tv-events] %s ignoré : attendu un identifiant court "
                "(lettres, chiffres, `_ . : -`, 64 caractères max)",
                name,
            )
    return _DEFAULT_DEVICE_ID


# ── Normalisation ─────────────────────────────────────────────────────────────


def _slug(value: Any, *, limit: int, fallback: str = "") -> str:
    """Réduit une valeur à un identifiant court, sans espace ni ponctuation libre."""
    text = _SLUG_PATTERN.sub("_", str(value or "").strip())
    text = text.strip("_")[:limit]
    return text or fallback


def _text(value: Any, limit: int) -> str:
    """Tronque une chaîne au plafond configuré, marqueur inclus."""
    text = str(value)
    if len(text) <= limit:
        return text
    marker = "…"
    return text[: max(0, limit - len(marker))] + marker


def _coerce_value(value: Any, *, limit: int, depth: int = 0) -> Any:
    """Rend une valeur sérialisable JSON, bornée en taille et en profondeur."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _text(value, limit)
    if depth >= _MAX_PAYLOAD_DEPTH:
        return _text(value, limit)
    if isinstance(value, Mapping):
        return {
            _slug(key, limit=_MAX_SOURCE_CHARS, fallback=_UNKNOWN): _coerce_value(
                item, limit=limit, depth=depth + 1
            )
            for key, item in list(value.items())[:_MAX_MAPPING_KEYS]
        }
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [
            _coerce_value(item, limit=limit, depth=depth + 1)
            for item in list(value)[:_MAX_SEQUENCE_ITEMS]
        ]
    return _text(value, limit)


def sanitize_payload(event_type: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Filtre, redacte et borne un payload avant diffusion.

    Ordre volontaire : allowlist de clés d'abord (le moins de données possible
    traverse la redaction), retrait des transcriptions ensuite, redaction des
    secrets enfin, puis bornage des valeurs.
    """
    allowed = TV_PAYLOAD_FIELDS.get(event_type)
    if allowed is None:
        raise TvEventError(
            f"Type d'événement TV inconnu : {event_type!r} — "
            f"types déclarés : {sorted(TV_EVENT_TYPES)}"
        )
    if not payload:
        return {}

    filtered = {key: value for key, value in payload.items() if key in allowed}
    if not transcripts_allowed():
        for field_name in TV_TRANSCRIPT_FIELDS:
            filtered.pop(field_name, None)

    redacted = redact_sensitive_mapping(filtered)
    limit = max_text_chars()
    return {
        str(key): _coerce_value(value, limit=limit)
        for key, value in redacted.items()
    }


@dataclass(frozen=True, slots=True)
class TvEvent:
    """Événement TV normalisé — contrat sérialisé stable.

    Attributes:
        type: Type `tv.*` appartenant à `TV_EVENT_TYPES`.
        timestamp: Horodatage Unix en secondes (float, UTC).
        device_id: Machine concernée par l'événement.
        state: État court et lisible (`listening`, `high`, `created`…).
        source: Composant émetteur, normalisé en identifiant court.
        payload: Données complémentaires, filtrées par `TV_PAYLOAD_FIELDS`.
        event_id: UUID4 permettant à un client de dédupliquer après reconnexion.
        schema_version: Version du contrat sérialisé.
    """

    type: str
    timestamp: float
    device_id: str
    state: str
    source: str
    payload: dict[str, Any]
    event_id: str
    schema_version: int = TV_EVENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le contrat public dans l'ordre documenté."""
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "type": self.type,
            "timestamp": self.timestamp,
            "device_id": self.device_id,
            "state": self.state,
            "source": self.source,
            "payload": dict(self.payload),
        }

    def to_json(self) -> str:
        """Rend la trame texte envoyée sur le canal."""
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    def byte_size(self) -> int:
        """Taille exacte de la trame, en octets UTF-8."""
        return len(self.to_json().encode("utf-8"))


def build_tv_event(
    event_type: str,
    *,
    state: str = "",
    payload: Mapping[str, Any] | None = None,
    device_id: str | None = None,
    source: str = "jarvis",
    timestamp: float | None = None,
) -> TvEvent:
    """Construit un événement TV valide ou lève `TvEventError`.

    Args:
        event_type: Type `tv.*` déclaré dans `TV_EVENT_TYPES`.
        state: État court ; normalisé en identifiant, tronqué à 32 caractères.
        payload: Données brutes, filtrées par l'allowlist du type.
        device_id: Machine concernée ; par défaut celle configurée.
        source: Composant émetteur.
        timestamp: Horodatage Unix ; par défaut l'instant courant.

    Raises:
        TvEventError: Si le type n'appartient pas au contrat TV.
    """
    if event_type not in TV_EVENT_TYPES:
        raise TvEventError(
            f"Type d'événement TV inconnu : {event_type!r} — "
            f"types déclarés : {sorted(TV_EVENT_TYPES)}"
        )
    return TvEvent(
        type=event_type,
        timestamp=float(timestamp if timestamp is not None else time.time()),
        device_id=_slug(
            device_id or default_device_id(),
            limit=_MAX_DEVICE_ID_CHARS,
            fallback=_DEFAULT_DEVICE_ID,
        ),
        state=_slug(state, limit=_MAX_STATE_CHARS, fallback=_UNKNOWN),
        source=_slug(source, limit=_MAX_SOURCE_CHARS, fallback=_UNKNOWN),
        payload=sanitize_payload(event_type, payload),
        event_id=str(uuid.uuid4()),
    )


def build_heartbeat_event(device_id: str | None = None) -> TvEvent:
    """Battement de cœur applicatif — prouve que le canal est toujours vivant."""
    return build_tv_event(
        TV_HEARTBEAT,
        state="alive",
        device_id=device_id,
        source="ws_tv",
    )


# ── Diffusion ─────────────────────────────────────────────────────────────────


class TvEventSubscription:
    """File bornée d'un abonné au canal TV.

    Le dépôt ne bloque jamais : une file pleine perd son événement le plus
    ancien, ce qui est le bon compromis pour un tableau de bord (l'état
    courant vaut mieux qu'un historique en retard). Les pertes sont comptées ;
    au-delà du budget, l'abonné est déclaré en débordement et le transport le
    déconnecte.
    """

    __slots__ = ("_max_dropped", "_queue", "dropped_events", "label")

    def __init__(self, *, label: str, maxsize: int, max_dropped: int) -> None:
        self.label = label
        self.dropped_events = 0
        self._max_dropped = max_dropped
        self._queue: asyncio.Queue[TvEvent] = asyncio.Queue(maxsize=maxsize)

    @property
    def pending(self) -> int:
        """Nombre d'événements en attente de lecture."""
        return self._queue.qsize()

    @property
    def is_overflowed(self) -> bool:
        """True quand le budget de pertes est dépassé."""
        return self.dropped_events > self._max_dropped

    def offer(self, event: TvEvent) -> bool:
        """Dépose un événement sans jamais bloquer ni lever.

        Returns:
            True si l'événement est en file, False si même le remplacement du
            plus ancien a échoué (file de taille nulle).
        """
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            pass

        self.dropped_events += 1
        try:
            self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return False
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:  # pragma: no cover - file vidée juste avant
            return False

    async def next_event(self, timeout: float) -> TvEvent | None:
        """Attend le prochain événement.

        Returns:
            L'événement, ou None si `timeout` s'écoule (créneau de heartbeat).
        """
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None


class TvEventHub:
    """Registre des abonnés au canal TV — diffusion sans retour."""

    __slots__ = ("_subscribers",)

    def __init__(self) -> None:
        self._subscribers: list[TvEventSubscription] = []

    @property
    def subscriber_count(self) -> int:
        """Nombre d'abonnés actifs."""
        return len(self._subscribers)

    def subscribe(self, *, label: str) -> TvEventSubscription:
        """Ouvre un abonnement borné par la configuration courante."""
        subscription = TvEventSubscription(
            label=label,
            maxsize=queue_maxsize(),
            max_dropped=max_dropped_events(),
        )
        self._subscribers.append(subscription)
        logger.info(
            "[tv-events] abonné ajouté (%s) — abonnés=%d",
            label,
            len(self._subscribers),
        )
        return subscription

    def unsubscribe(self, subscription: TvEventSubscription) -> None:
        """Retire un abonnement ; sans effet s'il est déjà parti."""
        try:
            self._subscribers.remove(subscription)
        except ValueError:
            return
        logger.info(
            "[tv-events] abonné retiré (%s, %d perdus) — abonnés=%d",
            subscription.label,
            subscription.dropped_events,
            len(self._subscribers),
        )

    def publish(self, event: TvEvent) -> int:
        """Diffuse un événement à tous les abonnés.

        Un événement trop volumineux est refusé plutôt que tronqué : mieux vaut
        une absence visible qu'un affichage amputé sans le dire.

        Returns:
            Nombre d'abonnés servis.
        """
        if not tv_events_enabled():
            return 0
        size = event.byte_size()
        limit = max_event_bytes()
        if size > limit:
            logger.warning(
                "[tv-events] événement %s non diffusé : %d octets > %d autorisés",
                event.type,
                size,
                limit,
            )
            return 0
        delivered = 0
        for subscription in tuple(self._subscribers):
            if subscription.offer(event):
                delivered += 1
        return delivered


tv_event_hub: Final[TvEventHub] = TvEventHub()


# ── Producteurs ───────────────────────────────────────────────────────────────


def publish_audio_daemon_state(raw_event: Mapping[str, Any]) -> TvEvent | None:
    """Traduit un message du daemon audio en état vocal TV.

    Appelée depuis un callback de diffusion : elle ne lève jamais, sous peine
    d'interrompre la boucle du daemon pour un écran de salon.

    Returns:
        L'événement diffusé, ou None si le message n'est pas un état daemon.
    """
    try:
        event_type = str(raw_event.get("type") or "")
        if not event_type.startswith(AUDIO_DAEMON_EVENT_PREFIX):
            return None
        event = build_tv_event(
            TV_VOICE_STATE,
            state=str(raw_event.get("state") or ""),
            payload=raw_event,
            source="audio_daemon",
        )
        tv_event_hub.publish(event)
        return event
    except Exception:
        logger.exception("[tv-events] état du daemon audio non diffusé")
        return None


def translate_bus_event(event: JarvisEvent) -> TvEvent | None:
    """Traduit un événement du bus applicatif, si et seulement s'il est autorisé.

    Returns:
        L'événement TV correspondant, ou None si le type n'est pas traduit.
    """
    tv_type = TV_BUS_EVENT_TRANSLATIONS.get(event.type)
    if tv_type is None:
        return None

    data = event.payload
    source = event.source or "event_bus"

    if tv_type == TV_NOTIFICATION:
        return build_tv_event(
            TV_NOTIFICATION,
            state=str(data.get("priority") or _UNKNOWN),
            payload={
                "notification_id": data.get("notification_id"),
                "priority": data.get("priority"),
                "notification_source": data.get("source"),
                "title": data.get("title"),
            },
            source=source,
        )

    if tv_type == TV_TASK:
        changes = data.get("changes")
        changes = changes if isinstance(changes, Mapping) else {}
        state = "created" if event.type == "task.created" else "updated"
        return build_tv_event(
            TV_TASK,
            state=state,
            payload={
                "task_id": data.get("task_id"),
                "title": data.get("title") or changes.get("title"),
                "priority": data.get("priority") or changes.get("priority"),
                "status": changes.get("status"),
                "due_date": data.get("due_date") or changes.get("due_date"),
                "category": data.get("category") or changes.get("category"),
            },
            source=source,
        )

    return build_tv_event(
        TV_SYSTEM,
        state=event.type.split(".", 1)[-1],
        payload={
            "service": data.get("service") or data.get("name") or source,
            "detail": data.get("detail") or data.get("message") or data.get("error"),
        },
        source=source,
    )


@event_bus.on(tuple(TV_BUS_EVENT_TRANSLATIONS))
def relay_bus_event_to_tv(event: JarvisEvent) -> None:
    """Pont bus applicatif → canal TV, limité aux types traduits."""
    try:
        tv_event = translate_bus_event(event)
    except TvEventError:
        logger.exception("[tv-events] traduction refusée pour %s", event.type)
        return
    if tv_event is not None:
        tv_event_hub.publish(tv_event)

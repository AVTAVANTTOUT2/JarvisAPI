"""Contrat de santé unifié — agrégation des composants réellement vérifiables.

Ce module est la **seule** source de vérité de l'état de santé de JARVIS. Il
n'invente aucune métrique : il rappelle les primitives déjà existantes
(``jarvis.resource_guard`` pour la mémoire, ``jarvis.audio.tts`` pour le moteur
vocal, ``database`` pour SQLite, ``jarvis.event_bus`` pour le bus) et se
contente de les traduire en états explicites.

Trois décisions structurantes :

- **Aucun appel réseau pendant la requête.** Les sondes lisent un fichier, une
  base locale, un état en mémoire ou lancent un utilitaire système borné. Le
  relevé complet des processus reste dans ``GET /api/supervisor/resources`` —
  seuls la RAM libre et le niveau sont repris ici, calculés par les mêmes
  fonctions.
- **Aucun faux vert.** Un composant que la requête ne peut pas prouver est
  ``unknown``, jamais ``healthy``. Les poids du moteur vocal pèsent plusieurs
  gigaoctets : les charger pour répondre à une sonde serait absurde, donc le
  TTS reste ``unknown`` tant qu'un tour de parole réel ne l'a pas exercé.
- **Les raisons sont un vocabulaire fermé.** Une exception porte un chemin, un
  identifiant ou une topologie ; elle part dans les journaux, jamais dans la
  réponse. Le client reçoit un code de ``PUBLIC_REASONS``.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_PROJECT_DIR = Path(__file__).resolve().parents[1]

# ── Vocabulaire d'état ───────────────────────────────────────

HEALTHY = "healthy"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"
UNKNOWN = "unknown"

VALID_STATES = frozenset({HEALTHY, DEGRADED, UNAVAILABLE, UNKNOWN})

#: Codes publics autorisés. Toute autre valeur est remplacée par
#: ``internal_error`` et le détail réel part dans les journaux serveur.
PUBLIC_REASONS = frozenset(
    {
        "internal_error",
        "probe_timeout",
        "database_unreachable",
        "database_query_failed",
        "event_bus_loop_unbound",
        "event_bus_queue_saturated",
        "resource_guard_disabled",
        "memory_probe_unavailable",
        "memory_low",
        "memory_critical",
        "stt_unavailable",
        "tts_provider_misconfigured",
        "tts_engine_not_probed",
    }
)

#: Un composant critique en panne rend l'application inutilisable ; un
#: composant non critique dégrade l'expérience sans la supprimer.
CRITICAL_COMPONENTS = frozenset({"backend", "database"})

#: Budget par sonde. Une page de diagnostic qui pend est un diagnostic de
#: moins : mieux vaut un composant `unknown` daté qu'une requête suspendue.
PROBE_TIMEOUT_S = 3.0

#: Durée de validité du relevé partagé. Plusieurs onglets ouverts sur la page
#: de santé ne doivent pas multiplier les `ps`, les `memory_pressure` et les
#: ouvertures SQLite.
CACHE_TTL_S = 5.0

_MAX_DETAIL_STR = 120

_PROCESS_STARTED_AT = time.monotonic()


@dataclass(frozen=True)
class ComponentHealth:
    """État d'un composant — jamais de contenu d'exception, jamais de chemin."""

    name: str
    state: str
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state if self.state in VALID_STATES else UNKNOWN,
            "critical": self.name in CRITICAL_COMPONENTS,
            "reason": public_reason(self.reason),
            "details": public_details(self.details),
        }


def public_reason(reason: str | None) -> str | None:
    """Réduit une raison au vocabulaire public."""
    if reason is None:
        return None
    if reason in PUBLIC_REASONS:
        return reason
    logger.warning("[health] raison non publique remplacée : %r", reason)
    return "internal_error"


def public_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """Filet de sécurité : seuls des scalaires courts sortent d'ici.

    Les sondes construisent déjà leurs détails champ par champ. Ce filtre
    existe pour qu'une sonde future ne puisse pas laisser passer un objet, une
    liste de processus ou un chemin absolu par simple distraction.
    """
    if not details:
        return {}
    clean: dict[str, Any] = {}
    for key, value in details.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, bool) or value is None:
            clean[key] = value
        elif isinstance(value, (int, float)):
            clean[key] = value
        elif isinstance(value, str):
            clean[key] = value[:_MAX_DETAIL_STR]
        else:
            logger.warning("[health] détail non scalaire ignoré : %s", key)
    return clean


# ── Sondes ───────────────────────────────────────────────────


def probe_backend() -> ComponentHealth:
    """Le processus qui répond est vivant — la seule évidence gratuite.

    L'uptime est une durée relative : il ne date pas la machine et ne dit rien
    de son horloge ni de son hôte.
    """
    return ComponentHealth(
        name="backend",
        state=HEALTHY,
        details={"uptime_s": round(time.monotonic() - _PROCESS_STARTED_AT, 1)},
    )


def probe_database() -> ComponentHealth:
    """Ouvre la base, exécute une requête triviale, mesure la latence."""
    from database import get_db

    started = time.perf_counter()
    try:
        with get_db() as conn:
            conn.execute("SELECT 1").fetchone()
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()
    except sqlite3.Error as exc:
        logger.error("[health] base SQLite injoignable : %s", exc)
        return ComponentHealth(
            name="database", state=UNAVAILABLE, reason="database_query_failed"
        )
    except OSError as exc:
        logger.error("[health] base SQLite illisible : %s", exc)
        return ComponentHealth(
            name="database", state=UNAVAILABLE, reason="database_unreachable"
        )

    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    mode = journal_mode[0] if journal_mode else None
    return ComponentHealth(
        name="database",
        state=HEALTHY,
        details={
            "latency_ms": latency_ms,
            "journal_mode": str(mode).lower() if mode else None,
        },
    )


def probe_event_bus() -> ComponentHealth:
    """Observe le bus sans rien émettre.

    Le bus n'expose pas de compteurs publics ; on lit ce qui existe avec des
    valeurs de repli, pour qu'une évolution interne du bus dégrade la sonde
    plutôt que la requête.
    """
    from jarvis.event_bus import event_bus

    subscribers = len(getattr(event_bus, "_subscribers", ()) or ())
    handler_queues = getattr(event_bus, "_handler_queues", {}) or {}
    queued = sum(queue.qsize() for queue in handler_queues.values())
    saturated = sum(1 for queue in handler_queues.values() if queue.full())
    loop_bound = getattr(event_bus, "_loop", None) is not None

    details = {
        "subscribers": subscribers,
        "handler_queues": len(handler_queues),
        "queued_events": queued,
        "saturated_queues": saturated,
        "loop_bound": loop_bound,
    }

    if not loop_bound:
        # Les émissions synchrones (threads scheduler, daemons) ne peuvent pas
        # rejoindre la boucle applicative tant qu'elle n'est pas liée.
        return ComponentHealth(
            name="event_bus",
            state=DEGRADED,
            reason="event_bus_loop_unbound",
            details=details,
        )
    if saturated:
        return ComponentHealth(
            name="event_bus",
            state=DEGRADED,
            reason="event_bus_queue_saturated",
            details=details,
        )
    return ComponentHealth(name="event_bus", state=HEALTHY, details=details)


def probe_resources() -> ComponentHealth:
    """Reprend le garde-fou existant : mêmes fonctions, aucune action.

    Aucun processus n'est listé ici. L'inventaire complet reste l'affaire de
    ``GET /api/supervisor/resources`` ; le dupliquer donnerait deux vérités
    concurrentes sur la même machine.
    """
    import config
    from jarvis.resource_guard import config_from_settings, memory_level, read_memory_free_mb

    guard_config = config_from_settings(config, project_dir=_PROJECT_DIR)
    if not guard_config.enabled:
        return ComponentHealth(
            name="resources", state=UNKNOWN, reason="resource_guard_disabled"
        )

    free_mb = read_memory_free_mb()
    if free_mb is None:
        # `memory_level(None, ...)` répond « ok » : c'est le bon défaut pour un
        # garde-fou qui ne doit rien tuer sans preuve, et le mauvais pour une
        # sonde de santé, qui doit dire qu'elle n'a pas mesuré.
        return ComponentHealth(
            name="resources", state=UNKNOWN, reason="memory_probe_unavailable"
        )

    details = {
        "free_mb": round(free_mb, 1),
        "warn_free_mb": float(guard_config.warn_free_mb),
        "critical_free_mb": float(guard_config.critical_free_mb),
    }
    # Le seuil est celui du garde-fou, pas un second barème : une alerte de
    # santé et une action du garde-fou se déclenchent sur la même mesure.
    level = memory_level(free_mb, guard_config)
    if level == "critical":
        return ComponentHealth(
            name="resources", state=UNAVAILABLE, reason="memory_critical", details=details
        )
    if level == "warn":
        return ComponentHealth(
            name="resources", state=DEGRADED, reason="memory_low", details=details
        )
    return ComponentHealth(name="resources", state=HEALTHY, details=details)


def probe_speech_to_text() -> ComponentHealth:
    """Disponibilité réelle du moteur STT local, telle que le module la connaît."""
    try:
        from audio import stt
    except ImportError:
        stt = None  # type: ignore[assignment]

    available = stt is not None and bool(getattr(stt, "available", False))
    if not available:
        return ComponentHealth(
            name="speech_to_text", state=UNAVAILABLE, reason="stt_unavailable"
        )
    try:
        engine = str(stt.get_backend_name())
    except Exception as exc:  # noqa: BLE001 - un moteur bavard ne casse pas la sonde
        logger.warning("[health] moteur STT illisible : %s", exc)
        engine = None
    return ComponentHealth(
        name="speech_to_text", state=HEALTHY, details={"engine": engine}
    )


def probe_text_to_speech() -> ComponentHealth:
    """Vérifie la configuration du moteur vocal, jamais ses poids.

    ``info()`` n'ouvre ni modèle ni sous-processus. On sait donc dire qu'une
    configuration est invalide, mais pas qu'un modèle est présent et
    fonctionnel : l'état reste ``unknown``, avec la raison qui l'explique.
    """
    from jarvis.audio.tts import get_local_tts_provider, load_tts_settings

    settings = load_tts_settings()
    provider_name = str(getattr(settings, "provider", "") or "")
    try:
        info = get_local_tts_provider(settings).info()
    except Exception as exc:  # noqa: BLE001 - fournisseur inconnu ou runtime absent
        logger.warning("[health] fournisseur TTS invalide : %s", exc)
        return ComponentHealth(
            name="text_to_speech",
            state=UNAVAILABLE,
            reason="tts_provider_misconfigured",
            details={"provider": provider_name},
        )

    # `model` et `voice` peuvent porter un chemin local : ils restent hors de
    # la réponse. Le nom du moteur et le mode de diffusion suffisent au
    # diagnostic.
    return ComponentHealth(
        name="text_to_speech",
        state=UNKNOWN,
        reason="tts_engine_not_probed",
        details={
            "provider": info.provider,
            "backend": info.backend,
            "device": info.device,
            "streaming": info.streaming,
            "offline": bool(info.offline),
        },
    )


#: Ordre d'affichage stable : le contrat JSON ne doit pas dépendre du hasard
#: d'un dictionnaire ou de l'ordonnancement asyncio.
PROBES: tuple[tuple[str, Callable[[], ComponentHealth]], ...] = (
    ("backend", probe_backend),
    ("database", probe_database),
    ("event_bus", probe_event_bus),
    ("resources", probe_resources),
    ("speech_to_text", probe_speech_to_text),
    ("text_to_speech", probe_text_to_speech),
)


# ── Agrégation ───────────────────────────────────────────────


def aggregate_state(components: list[ComponentHealth]) -> str:
    """État global — une panne partielle donne ``degraded``, pas ``unavailable``.

    Un ``unknown`` non critique ne dégrade pas le global : il serait malhonnête
    de peindre l'application en rouge parce qu'une sonde n'a rien pu mesurer.
    Il reste visible composant par composant et compté dans le résumé, donc
    aucune ignorance n'est présentée comme un succès.
    """
    if not components:
        return UNKNOWN
    for component in components:
        if component.name in CRITICAL_COMPONENTS and component.state == UNAVAILABLE:
            return UNAVAILABLE
    for component in components:
        if component.state in (UNAVAILABLE, DEGRADED):
            return DEGRADED
        if component.name in CRITICAL_COMPONENTS and component.state == UNKNOWN:
            return DEGRADED
    return HEALTHY


def summarize(components: list[ComponentHealth]) -> dict[str, int]:
    summary = {HEALTHY: 0, DEGRADED: 0, UNAVAILABLE: 0, UNKNOWN: 0}
    for component in components:
        state = component.state if component.state in VALID_STATES else UNKNOWN
        summary[state] += 1
    return summary


async def _run_probe(name: str, probe: Callable[[], ComponentHealth]) -> ComponentHealth:
    """Exécute une sonde bloquante hors boucle, avec un budget de temps."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(probe), timeout=PROBE_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning("[health] sonde %s expirée (%.1fs)", name, PROBE_TIMEOUT_S)
        return ComponentHealth(name=name, state=UNKNOWN, reason="probe_timeout")
    except Exception as exc:  # noqa: BLE001 - une sonde ne casse jamais la page
        logger.exception("[health] sonde %s en échec : %s", name, exc)
        return ComponentHealth(name=name, state=UNKNOWN, reason="internal_error")


async def collect_health() -> dict[str, Any]:
    """Relevé complet — toutes les sondes, même si l'une d'elles échoue."""
    started = time.perf_counter()
    components = [await _run_probe(name, probe) for name, probe in PROBES]
    return {
        "status": aggregate_state(components),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "summary": summarize(components),
        "components": [component.to_public_dict() for component in components],
    }


# ── Cache court partagé ──────────────────────────────────────

_cache: dict[str, Any] | None = None
_cache_expires_at: float = 0.0
_cache_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


def reset_cache() -> None:
    """Vide le relevé partagé — utilisé par les tests et au redémarrage."""
    global _cache, _cache_expires_at, _cache_lock
    _cache = None
    _cache_expires_at = 0.0
    _cache_lock = None


async def get_health(*, force: bool = False) -> dict[str, Any]:
    """Relevé partagé, valide ``CACHE_TTL_S`` secondes.

    Le cache borne le coût d'une page ouverte en permanence : sans lui, chaque
    onglet paierait son propre ``memory_pressure`` et sa propre ouverture
    SQLite.
    """
    global _cache, _cache_expires_at

    now = time.monotonic()
    if not force and _cache is not None and now < _cache_expires_at:
        return _cache

    async with _get_lock():
        now = time.monotonic()
        if not force and _cache is not None and now < _cache_expires_at:
            return _cache
        report = await collect_health()
        _cache = report
        _cache_expires_at = time.monotonic() + CACHE_TTL_S
        return report


__all__ = [
    "CACHE_TTL_S",
    "CRITICAL_COMPONENTS",
    "DEGRADED",
    "HEALTHY",
    "PROBES",
    "PROBE_TIMEOUT_S",
    "PUBLIC_REASONS",
    "UNAVAILABLE",
    "UNKNOWN",
    "VALID_STATES",
    "ComponentHealth",
    "aggregate_state",
    "collect_health",
    "get_health",
    "probe_backend",
    "probe_database",
    "probe_event_bus",
    "probe_resources",
    "probe_speech_to_text",
    "probe_text_to_speech",
    "public_details",
    "public_reason",
    "reset_cache",
    "summarize",
]

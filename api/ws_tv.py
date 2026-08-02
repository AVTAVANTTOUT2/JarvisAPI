"""Canal WebSocket TV — `/ws/tv/events`, authentifié et strictement descendant.

Pourquoi un second endpoint plutôt qu'un élargissement de `/ws` : le canal de
chat transporte des messages, des commandes, de l'audio et des confirmations
d'action. Y brancher un écran mural reviendrait à donner à un appareil allumé
en permanence, visible depuis le salon, le droit de lire des conversations et
d'émettre des commandes. Ici, rien ne remonte : toute trame reçue est une
violation du contrat, et sa répétition ferme la connexion.

Authentification : le jeton privé du canal supervisor (`X-Jarvis-Control-Token`,
fichier 0600 à côté de la base), restreint à la boucle locale — exactement la
frontière déjà utilisée par `/api/control/*`. Un navigateur ne peut pas poser
cet en-tête sur un handshake WebSocket ; si une origine navigateur est tout de
même présente, elle doit être exacte, faute de quoi la connexion est fermée
avant toute comparaison de jeton.

Le refus est fermé après acceptation du handshake, volontairement : Starlette
transforme une fermeture antérieure en un HTTP 403 opaque, et le relais TV a
besoin de distinguer « reviens plus tard » d'« reconfigure ton jeton ».
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final

from fastapi import WebSocket, WebSocketDisconnect

import config
from api.middleware import browser_websocket_origin_allowed
from core.supervisor_auth import (
    SUPERVISOR_CONTROL_HEADER,
    verify_supervisor_control_token,
)
from jarvis.tv_events import (
    TvEventSubscription,
    build_heartbeat_event,
    tv_event_hub,
    tv_events_enabled,
)

logger = logging.getLogger("jarvis.ws_tv")

#: Chemin du canal. Distinct de `/ws`, qui reste le canal de chat et de commande.
TV_EVENTS_WS_PATH: Final[str] = "/ws/tv/events"

# Codes de fermeture applicatifs (plage 4000-4999 réservée aux applications).
CLOSE_UNAUTHORIZED: Final[int] = 4401
CLOSE_FORBIDDEN_ORIGIN: Final[int] = 4403
CLOSE_READ_ONLY_VIOLATION: Final[int] = 4405
CLOSE_SLOW_CONSUMER: Final[int] = 4408
CLOSE_PAYLOAD_TOO_LARGE: Final[int] = 4413
CLOSE_TOO_MANY_CONNECTIONS: Final[int] = 4429

#: Adresses acceptées comme locales. Le canal ne franchit pas la machine : un
#: écran distant passe par le serveur TV, qui tourne sur ce Mac.
LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "::1", "localhost"})

# Motifs de refus journalisés. Ce sont des étiquettes fermées : elles ne
# contiennent jamais de valeur fournie par le client, donc jamais de jeton.
REFUSAL_ORIGIN: Final[str] = "origine_navigateur_refusee"
REFUSAL_REMOTE: Final[str] = "hors_boucle_locale"
REFUSAL_TOKEN_MISSING: Final[str] = "jeton_absent"
REFUSAL_TOKEN_INVALID: Final[str] = "jeton_invalide"
REFUSAL_DISABLED: Final[str] = "canal_desactive"

_REFUSAL_CLOSE_CODES: Final[dict[str, int]] = {
    REFUSAL_ORIGIN: CLOSE_FORBIDDEN_ORIGIN,
    REFUSAL_REMOTE: CLOSE_UNAUTHORIZED,
    REFUSAL_TOKEN_MISSING: CLOSE_UNAUTHORIZED,
    REFUSAL_TOKEN_INVALID: CLOSE_UNAUTHORIZED,
    REFUSAL_DISABLED: CLOSE_UNAUTHORIZED,
}

_active_connections: set[WebSocket] = set()


# ── Réglages ──────────────────────────────────────────────────────────────────


def _positive_int(name: str, default: int, *, minimum: int = 1) -> int:
    """Lit un entier de configuration en refusant les valeurs absurdes."""
    try:
        value = int(getattr(config, name, default))
    except (TypeError, ValueError):
        logger.warning("[ws/tv] %s illisible — repli sur %d", name, default)
        return default
    return max(value, minimum)


def _positive_float(name: str, default: float, *, minimum: float = 0.1) -> float:
    """Lit un délai de configuration en refusant les valeurs nulles ou négatives."""
    try:
        value = float(getattr(config, name, default))
    except (TypeError, ValueError):
        logger.warning("[ws/tv] %s illisible — repli sur %.1f", name, default)
        return default
    return max(value, minimum)


def max_connections() -> int:
    """Nombre de canaux TV simultanés autorisés."""
    return _positive_int("TV_WS_MAX_CONNECTIONS", 4)


def heartbeat_seconds() -> float:
    """Période du battement de cœur en l'absence d'événement."""
    return _positive_float("TV_WS_HEARTBEAT_SECONDS", 20.0)


def send_timeout_seconds() -> float:
    """Délai au-delà duquel un client est considéré comme bloqué."""
    return _positive_float("TV_WS_SEND_TIMEOUT_SECONDS", 5.0)


def max_client_message_bytes() -> int:
    """Taille maximale tolérée pour une trame entrante avant fermeture immédiate."""
    return _positive_int("TV_WS_MAX_CLIENT_MESSAGE_BYTES", 4096, minimum=1)


def max_client_violations() -> int:
    """Nombre de trames entrantes tolérées avant fermeture du canal."""
    return _positive_int("TV_WS_MAX_CLIENT_VIOLATIONS", 3)


def active_connection_count() -> int:
    """Nombre de canaux TV actuellement ouverts."""
    return len(_active_connections)


# ── Authentification ──────────────────────────────────────────────────────────


def _client_host(ws: WebSocket) -> str:
    """Adresse du pair TCP, sans faire confiance au moindre en-tête."""
    return ws.client.host if ws.client else ""


def _connection_label(ws: WebSocket) -> str:
    """Étiquette de journalisation : jamais de jeton, jamais de cookie."""
    client = ws.client
    if client is None:
        return "client_inconnu"
    return f"{client.host}:{client.port}"


def authorize(ws: WebSocket) -> str | None:
    """Autorise un handshake TV.

    L'ordre est délibéré : une origine navigateur invalide est rejetée avant
    toute lecture du jeton, pour qu'une page hostile n'obtienne aucun signal
    sur la validité d'un secret.

    Returns:
        None si la connexion est autorisée, sinon l'étiquette du refus.
    """
    if not tv_events_enabled():
        return REFUSAL_DISABLED

    if ws.headers.get("origin") and not browser_websocket_origin_allowed(ws):
        return REFUSAL_ORIGIN

    if _client_host(ws) not in LOOPBACK_HOSTS:
        return REFUSAL_REMOTE

    token = ws.headers.get(SUPERVISOR_CONTROL_HEADER)
    if not token:
        return REFUSAL_TOKEN_MISSING
    if not verify_supervisor_control_token(token):
        return REFUSAL_TOKEN_INVALID
    return None


async def _refuse(ws: WebSocket, refusal: str) -> None:
    """Ferme un handshake refusé avec le code applicatif correspondant."""
    code = _REFUSAL_CLOSE_CODES.get(refusal, CLOSE_UNAUTHORIZED)
    logger.error(
        "[ws/tv] connexion refusée — motif=%s code=%d client=%s",
        refusal,
        code,
        _connection_label(ws),
    )
    await _accept_then_close(ws, code)


async def _accept_then_close(ws: WebSocket, code: int) -> None:
    """Accepte puis ferme, pour que le code applicatif atteigne le client."""
    try:
        await ws.accept()
        await ws.close(code=code)
    except (RuntimeError, WebSocketDisconnect) as exc:
        logger.debug("[ws/tv] fermeture %d sans pair actif : %s", code, exc)


# ── Boucles de transport ──────────────────────────────────────────────────────


class _ChannelClosure:
    """Décision de fermeture partagée entre le lecteur et l'émetteur."""

    __slots__ = ("_event", "code", "reason")

    def __init__(self) -> None:
        self.code: int | None = None
        self.reason: str = ""
        self._event = asyncio.Event()

    @property
    def triggered(self) -> bool:
        """True dès qu'une fermeture a été demandée."""
        return self._event.is_set()

    def request(self, code: int | None, reason: str) -> None:
        """Enregistre la première demande de fermeture ; les suivantes sont ignorées."""
        if self._event.is_set():
            return
        self.code = code
        self.reason = reason
        self._event.set()


def _packet_size(packet: dict[str, Any]) -> int:
    """Taille en octets d'une trame entrante, texte ou binaire."""
    text = packet.get("text")
    if isinstance(text, str):
        return len(text.encode("utf-8"))
    payload = packet.get("bytes")
    if isinstance(payload, (bytes, bytearray)):
        return len(payload)
    return 0


async def _enforce_read_only(
    ws: WebSocket,
    closure: _ChannelClosure,
    label: str,
) -> None:
    """Consomme les trames entrantes pour les refuser.

    Le canal n'a pas de protocole client : il n'existe aucune trame valide à
    envoyer. La boucle sert donc à deux choses — détecter la déconnexion
    immédiatement, et fermer un client qui insiste à écrire.
    """
    violations = 0
    limit = max_client_violations()
    size_limit = max_client_message_bytes()

    while True:
        packet = await ws.receive()
        if packet.get("type") == "websocket.disconnect":
            closure.request(None, "deconnexion_client")
            return

        size = _packet_size(packet)
        if size > size_limit:
            logger.warning(
                "[ws/tv] trame entrante de %d octets (> %d) — fermeture, client=%s",
                size,
                size_limit,
                label,
            )
            closure.request(CLOSE_PAYLOAD_TOO_LARGE, "trame_trop_volumineuse")
            return

        violations += 1
        logger.warning(
            "[ws/tv] écriture client ignorée (%d/%d) — canal en lecture seule, client=%s",
            violations,
            limit,
            label,
        )
        if violations >= limit:
            closure.request(CLOSE_READ_ONLY_VIOLATION, "ecritures_repetees")
            return


async def _stream_events(
    ws: WebSocket,
    subscription: TvEventSubscription,
    closure: _ChannelClosure,
    label: str,
) -> None:
    """Diffuse les événements TV et le battement de cœur jusqu'à fermeture."""
    heartbeat = heartbeat_seconds()
    timeout = send_timeout_seconds()

    while not closure.triggered:
        event = await subscription.next_event(timeout=heartbeat)
        if closure.triggered:
            return
        if subscription.is_overflowed:
            logger.warning(
                "[ws/tv] client lent déconnecté — %d événements perdus, client=%s",
                subscription.dropped_events,
                label,
            )
            closure.request(CLOSE_SLOW_CONSUMER, "client_lent")
            return
        if event is None:
            event = build_heartbeat_event()
        try:
            await asyncio.wait_for(ws.send_text(event.to_json()), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "[ws/tv] envoi expiré après %.1fs — client lent déconnecté, client=%s",
                timeout,
                label,
            )
            closure.request(CLOSE_SLOW_CONSUMER, "envoi_expire")
            return
        except (RuntimeError, WebSocketDisconnect) as exc:
            logger.debug("[ws/tv] envoi impossible (%s) — client=%s", exc, label)
            closure.request(None, "pair_absent")
            return


def _report_failures(tasks: set[asyncio.Task[None]], label: str) -> None:
    """Journalise l'échec réel d'une tâche terminée, hors annulation attendue."""
    for task in tasks:
        if task.cancelled():
            continue
        error = task.exception()
        if error is None or isinstance(error, WebSocketDisconnect):
            continue
        logger.error(
            "[ws/tv] tâche %s terminée en erreur (%s) — client=%s",
            task.get_name(),
            type(error).__name__,
            label,
            exc_info=error,
        )


async def _cancel(tasks: set[asyncio.Task[None]]) -> None:
    """Annule les tâches encore actives et attend leur sortie effective."""
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def tv_events_websocket(ws: WebSocket) -> None:
    """Endpoint `/ws/tv/events` — flux TV authentifié, en lecture seule.

    Aucune donnée entrante n'est interprétée : le client ne peut ni s'abonner à
    un filtre, ni piloter la TV, ni influencer le serveur d'une quelconque
    façon. Les seuls leviers sont la configuration serveur et la fermeture.
    """
    refusal = authorize(ws)
    if refusal is not None:
        await _refuse(ws, refusal)
        return

    label = _connection_label(ws)
    if len(_active_connections) >= max_connections():
        logger.warning(
            "[ws/tv] connexion refusée — %d canaux déjà ouverts, client=%s",
            len(_active_connections),
            label,
        )
        await _accept_then_close(ws, CLOSE_TOO_MANY_CONNECTIONS)
        return

    # Le créneau est réservé et l'abonnement ouvert avant le premier `await` :
    # deux handshakes simultanés ne peuvent pas franchir ensemble la limite, et
    # aucun événement ne se glisse entre l'acceptation et l'inscription au hub.
    _active_connections.add(ws)
    subscription = tv_event_hub.subscribe(label=label)
    closure = _ChannelClosure()
    try:
        await ws.accept()
    except (RuntimeError, WebSocketDisconnect) as exc:
        _active_connections.discard(ws)
        tv_event_hub.unsubscribe(subscription)
        logger.debug("[ws/tv] handshake abandonné (%s) — client=%s", exc, label)
        return

    logger.info("[ws/tv] canal ouvert — client=%s canaux=%d", label, len(_active_connections))

    reader = asyncio.create_task(
        _enforce_read_only(ws, closure, label), name="tv-ws-reader"
    )
    writer = asyncio.create_task(
        _stream_events(ws, subscription, closure, label), name="tv-ws-writer"
    )
    try:
        done, pending = await asyncio.wait(
            {reader, writer}, return_when=asyncio.FIRST_COMPLETED
        )
        await _cancel(pending)
        _report_failures(done, label)
    finally:
        # Filet en cas d'annulation de l'endpoint lui-même (arrêt applicatif) :
        # aucune tâche ne doit survivre à la fermeture du canal.
        for task in (reader, writer):
            if not task.done():
                task.cancel()
        tv_event_hub.unsubscribe(subscription)
        _active_connections.discard(ws)
        if closure.code is not None:
            try:
                await ws.close(code=closure.code)
            except (RuntimeError, WebSocketDisconnect) as exc:
                logger.debug("[ws/tv] fermeture tardive ignorée (%s)", exc)
        logger.info(
            "[ws/tv] canal fermé — client=%s motif=%s canaux=%d",
            label,
            closure.reason or "fin_normale",
            len(_active_connections),
        )

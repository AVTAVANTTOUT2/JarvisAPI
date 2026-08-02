"""Classification des échecs du TTS réseau (Edge) : injoignable ou cassé.

Deux familles d'échecs exigent des réactions opposées, et les confondre coûte
cher des deux côtés :

``NETWORK_UNAVAILABLE``
    La machine ne peut pas joindre le service : DNS muet, route absente,
    connexion refusée, délai dépassé, interception TLS par un proxy. Rien
    n'est cassé dans JARVIS. En production on journalise un avertissement et
    on replie sur un moteur local ; en test d'intégration, c'est le seul motif
    légitime d'ignorer un scénario réseau.

``FUNCTIONAL``
    Le service a répondu, mais le contrat est rompu : authentification
    refusée, réponse inattendue, protocole modifié, aucun audio produit.
    C'est une régression à corriger. En production on journalise une erreur ;
    en test, on échoue — jamais d'``skip``.

Cas particulier assumé : un proxy qui refuse le ``CONNECT`` répond en HTTP,
donc au sens d'``aiohttp`` « le serveur a répondu ». Ce n'est pourtant pas le
service TTS qui a parlé : ``ClientHttpProxyError`` est donc classé
injoignable, avant la règle générale.

Ce module n'importe ni ``edge_tts`` ni ``aiohttp`` : les deux sont optionnels
au runtime. Les types d'exceptions sont résolus dans ``sys.modules``, donc
uniquement si la bibliothèque concernée est déjà chargée — ce qui est
nécessairement le cas quand c'est elle qui a levé l'exception.
"""

from __future__ import annotations

import errno
import socket
import ssl
import sys
from collections.abc import Iterator
from enum import Enum
from types import ModuleType
from typing import Final

# Une chaîne de causes plus profonde relève d'une boucle : on arrête là.
MAX_CAUSE_DEPTH: Final[int] = 12
# Les messages aiohttp/SSL sont verbeux ; les journaux restent lisibles.
MAX_DETAIL_CHARS: Final[int] = 300


class TTSFailureKind(str, Enum):
    """Nature d'un échec de synthèse vocale réseau."""

    NETWORK_UNAVAILABLE = "network_unavailable"
    FUNCTIONAL = "functional"


_UNREACHABLE_ERRNOS: Final[frozenset[int]] = frozenset(
    code
    for code in (
        getattr(errno, name, None)
        for name in (
            "ECONNREFUSED",
            "ECONNRESET",
            "ECONNABORTED",
            "EHOSTDOWN",
            "EHOSTUNREACH",
            "ENETDOWN",
            "ENETUNREACH",
            "ENETRESET",
            "ENOTCONN",
            "EPIPE",
            "ETIMEDOUT",
        )
    )
    if code is not None
)

_TRANSPORT_BUILTINS: Final[tuple[type[BaseException], ...]] = (
    ConnectionError,  # refus, reset, abort, tube cassé
    TimeoutError,  # identique à asyncio.TimeoutError depuis Python 3.11
    socket.gaierror,  # résolution DNS impossible
    socket.herror,
    ssl.SSLCertVerificationError,  # interception TLS (proxy d'entreprise, bac à sable)
)

_AIOHTTP_MODULE: Final[str] = "aiohttp"
_EDGE_TTS_EXCEPTIONS_MODULE: Final[str] = "edge_tts.exceptions"

# Le proxy a refusé le tunnel : le service TTS n'a jamais été atteint.
_PROXY_BLOCKED_NAMES: Final[tuple[str, ...]] = ("ClientHttpProxyError",)

_AIOHTTP_TRANSPORT_NAMES: Final[tuple[str, ...]] = (
    "ClientConnectorCertificateError",
    "ClientConnectorSSLError",
    "ClientConnectorError",
    "ClientProxyConnectionError",
    "ClientOSError",
    "ServerConnectionError",
    "ServerDisconnectedError",
    "ServerTimeoutError",
)

# Le service a répondu : réseau opérationnel, contrat rompu.
_AIOHTTP_SERVER_RESPONDED_NAMES: Final[tuple[str, ...]] = (
    "WSServerHandshakeError",  # 401/403 : jeton ou API modifiés
    "ClientResponseError",
)

_EDGE_TTS_SERVER_RESPONDED_NAMES: Final[tuple[str, ...]] = (
    "NoAudioReceived",  # voix inconnue ou format de réponse modifié
    "UnexpectedResponse",
    "UnknownResponse",  # régression de parsing côté edge-tts
    "SkewAdjustmentError",  # 403 puis échec d'ajustement d'horloge
)


def _loaded_module(name: str) -> ModuleType | None:
    """Retourne un module déjà importé, sans jamais déclencher son import."""
    module = sys.modules.get(name)
    return module if isinstance(module, ModuleType) else None


def _resolve_exception_types(
    module_name: str, names: tuple[str, ...]
) -> tuple[type[BaseException], ...]:
    """Résout des noms d'exceptions dans un module optionnel déjà chargé."""
    module = _loaded_module(module_name)
    if module is None:
        return ()
    resolved: list[type[BaseException]] = []
    for name in names:
        candidate = getattr(module, name, None)
        if isinstance(candidate, type) and issubclass(candidate, BaseException):
            resolved.append(candidate)
    return tuple(resolved)


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """Parcourt l'exception puis ses causes (``__cause__`` sinon ``__context__``)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    for _ in range(MAX_CAUSE_DEPTH):
        if current is None or id(current) in seen:
            return
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _matches(exc: BaseException, module_name: str, names: tuple[str, ...]) -> bool:
    types = _resolve_exception_types(module_name, names)
    return bool(types) and isinstance(exc, types)


def _is_proxy_blocked(exc: BaseException) -> bool:
    return _matches(exc, _AIOHTTP_MODULE, _PROXY_BLOCKED_NAMES)


def _is_transport_failure(exc: BaseException) -> bool:
    if isinstance(exc, _TRANSPORT_BUILTINS):
        return True
    if _matches(exc, _AIOHTTP_MODULE, _AIOHTTP_TRANSPORT_NAMES):
        return True
    return isinstance(exc, OSError) and exc.errno in _UNREACHABLE_ERRNOS


def _is_server_response_failure(exc: BaseException) -> bool:
    return _matches(exc, _AIOHTTP_MODULE, _AIOHTTP_SERVER_RESPONDED_NAMES) or _matches(
        exc, _EDGE_TTS_EXCEPTIONS_MODULE, _EDGE_TTS_SERVER_RESPONDED_NAMES
    )


def classify_tts_failure(exc: BaseException) -> TTSFailureKind:
    """Qualifie un échec de synthèse réseau.

    L'ordre des règles est significatif : un refus de proxy prime sur « le
    serveur a répondu », qui prime lui-même sur les erreurs de transport (un
    service qui répond est joignable par définition). Le défaut est
    ``FUNCTIONAL`` : un échec non identifié doit être bruyant, pas silencieux.
    """
    chain = tuple(_exception_chain(exc))
    if any(_is_proxy_blocked(item) for item in chain):
        return TTSFailureKind.NETWORK_UNAVAILABLE
    if any(_is_server_response_failure(item) for item in chain):
        return TTSFailureKind.FUNCTIONAL
    if any(_is_transport_failure(item) for item in chain):
        return TTSFailureKind.NETWORK_UNAVAILABLE
    return TTSFailureKind.FUNCTIONAL


def is_network_unavailable(exc: BaseException) -> bool:
    """``True`` si l'échec vient de l'impossibilité de joindre le service."""
    return classify_tts_failure(exc) is TTSFailureKind.NETWORK_UNAVAILABLE


def describe_tts_failure(exc: BaseException) -> str:
    """Résumé d'échec exploitable en journal comme en motif d'``skip``.

    Un utilitaire de journalisation ne doit jamais lever : certaines exceptions
    de bibliothèques construisent leur ``__str__`` à partir d'attributs
    optionnels et échouent si l'objet est partiel.
    """
    kind = classify_tts_failure(exc)
    try:
        message = str(exc).strip()
    except Exception:  # noqa: BLE001 - __str__ tiers hors de notre contrôle
        message = ""
    message = message or exc.__class__.__name__
    if len(message) > MAX_DETAIL_CHARS:
        message = f"{message[:MAX_DETAIL_CHARS]}…"
    exc_type = type(exc)
    qualified = f"{exc_type.__module__}.{exc_type.__qualname__}"
    return f"{kind.value} · {qualified}: {message}"


__all__ = [
    "MAX_CAUSE_DEPTH",
    "MAX_DETAIL_CHARS",
    "TTSFailureKind",
    "classify_tts_failure",
    "describe_tts_failure",
    "is_network_unavailable",
]

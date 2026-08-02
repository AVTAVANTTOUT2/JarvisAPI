"""Garde-fou global : la suite standard se comporte comme une machine hors ligne.

Un test qui appelle un service tiers rend la suite non déterministe — il passe
au bureau, échoue derrière un proxy, et masque le jour où le service change
vraiment de contrat. Ici, toute connexion sortante est refusée : la suite
donne le même résultat avec ou sans réseau, et chaque tentative est nommée
dans le récapitulatif final.

Portée : ce fichier est à la racine du dépôt, donc actif pour `tests/`,
`jarvis/tests` et `agents/devagent`.

Ce qui reste autorisé :
  - la boucle locale (127.0.0.0/8, ::1) — plusieurs tests montent un serveur
    local et s'y connectent ;
  - les sockets Unix (`AF_UNIX`), qui ne quittent pas la machine ;
  - tout test marqué `external_network`, exclu de la suite standard.

`OutboundNetworkBlocked` dérive de `ConnectionError` (donc d'`OSError`) : c'est
exactement ce que verrait le code sur une machine sans réseau. Les replis hors
ligne déjà écrits s'appliquent normalement, et un test dont l'assertion dépend
d'une réponse distante échoue sur son assertion — pas sur une exception
exotique traversant les piles réseau. Les tentatives bloquées sont malgré tout
listées en fin de session : aucune n'est invisible.
"""

from __future__ import annotations

import errno
import ipaddress
import socket
import ssl
from collections.abc import Iterator
from typing import Any, Final

import pytest

LOOPBACK_HOSTNAMES: Final[frozenset[str]] = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback", ""}
)

MARKER_HINT: Final[str] = (
    "Marque le test `@pytest.mark.external_network` (et documente son exécution "
    "volontaire) ou remplace l'appel réseau par un double de test."
)

# nodeid → cibles refusées, dans l'ordre de la première tentative.
_BLOCKED_ATTEMPTS: Final[dict[str, list[str]]] = {}


class OutboundNetworkBlocked(ConnectionError):
    """Connexion sortante refusée pendant un test non marqué `external_network`."""

    def __init__(self, message: str) -> None:
        super().__init__(errno.ECONNREFUSED, message)


def record_blocked_attempt(node_id: str, target: str) -> None:
    """Mémorise une tentative refusée pour le récapitulatif de session."""
    targets = _BLOCKED_ATTEMPTS.setdefault(node_id, [])
    if target not in targets:
        targets.append(target)


def drain_blocked_attempts(node_id: str) -> list[str]:
    """Retire et retourne les tentatives d'un test (utilisé par son auto-test)."""
    return _BLOCKED_ATTEMPTS.pop(node_id, [])


def _is_loopback_address(address: Any) -> bool:
    """`True` si l'adresse cible ne quitte pas la machine."""
    if isinstance(address, (bytes, str)):
        # Chemin de socket Unix : jamais de trafic réseau.
        return True
    if not isinstance(address, tuple) or not address:
        # Familles exotiques (AF_NETLINK, AF_BLUETOOTH…) : hors sujet réseau IP.
        return True

    host = address[0]
    if not isinstance(host, (str, bytes)):
        return False
    if isinstance(host, bytes):
        host = host.decode("utf-8", errors="replace")

    normalized = host.strip().strip("[]").lower()
    if normalized in LOOPBACK_HOSTNAMES:
        return True
    # Une adresse IPv6 littérale peut porter un identifiant de zone (`::1%lo0`).
    normalized = normalized.split("%", 1)[0]
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _describe(address: Any) -> str:
    if isinstance(address, tuple) and len(address) >= 2:
        return f"{address[0]}:{address[1]}"
    return repr(address)


@pytest.fixture(autouse=True)
def _block_outbound_network(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Refuse toute connexion hors boucle locale pendant un test standard."""
    if request.node.get_closest_marker("external_network"):
        yield
        return

    node_id = request.node.nodeid
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection
    # `ssl.SSLSocket` redéfinit `connect` : sans ce second garde-fou, un socket
    # enveloppé avant connexion contournerait le blocage.
    real_ssl_connect = ssl.SSLSocket.connect
    real_ssl_connect_ex = ssl.SSLSocket.connect_ex

    def _guard(address: Any) -> None:
        if _is_loopback_address(address):
            return
        target = _describe(address)
        record_blocked_attempt(node_id, target)
        raise OutboundNetworkBlocked(
            f"Connexion sortante refusée vers {target} pendant {node_id} "
            f"(suite hors ligne). {MARKER_HINT}"
        )

    def connect(self: socket.socket, address: Any) -> None:
        _guard(address)
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        _guard(address)
        return real_connect_ex(self, address)

    def create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        _guard(address)
        return real_create_connection(address, *args, **kwargs)

    def ssl_connect(self: ssl.SSLSocket, address: Any) -> None:
        _guard(address)
        return real_ssl_connect(self, address)

    def ssl_connect_ex(self: ssl.SSLSocket, address: Any) -> int:
        _guard(address)
        return real_ssl_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(ssl.SSLSocket, "connect", ssl_connect)
    monkeypatch.setattr(ssl.SSLSocket, "connect_ex", ssl_connect_ex)

    yield


def pytest_terminal_summary(terminalreporter: Any) -> None:
    """Liste les tentatives de sortie réseau : bloquées, mais jamais masquées."""
    if not _BLOCKED_ATTEMPTS:
        return
    terminalreporter.section("Connexions sortantes refusées (suite hors ligne)")
    for node_id, targets in _BLOCKED_ATTEMPTS.items():
        terminalreporter.write_line(f"{node_id} → {', '.join(targets)}")
    terminalreporter.write_line(MARKER_HINT)

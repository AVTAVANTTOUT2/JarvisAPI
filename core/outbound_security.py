"""Validation des destinations HTTP contrôlées par un client ou un fichier."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit


class OutboundURLRejected(ValueError):
    """Destination sortante refusée avant toute connexion réseau."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _normalized_allowlist(values: str | Iterable[str]) -> tuple[str, ...]:
    raw_values = values.split(",") if isinstance(values, str) else values
    return tuple(
        normalized
        for raw in raw_values
        if (normalized := str(raw).strip().lower().rstrip("."))
    )


def _host_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    for pattern in allowed_hosts:
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if host.endswith(suffix) and host != suffix[1:]:
                return True
        elif host == pattern:
            return True
    return False


def _parse_https_destination(value: str) -> tuple[str, str]:
    """Retourne ``(url, host)`` pour une destination HTTPS/443 sans secret."""

    candidate = str(value or "").strip()
    try:
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise OutboundURLRejected("invalid_url", "URL de destination invalide") from exc

    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise OutboundURLRejected(
            "https_required",
            "La destination doit utiliser HTTPS sur le port 443, sans identifiants ni fragment",
        )
    return candidate, host


def _resolve_host_addresses(
    host: str,
    *,
    resolver: Callable[..., list[tuple]] | None = None,
) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        return {literal}
    except ValueError:
        pass
    try:
        lookup = resolver or socket.getaddrinfo
        answers = lookup(host, 443, type=socket.SOCK_STREAM)
        addresses = {
            ipaddress.ip_address(str(answer[4][0]).split("%", 1)[0])
            for answer in answers
            if answer[4]
        }
    except (OSError, ValueError) as exc:
        raise OutboundURLRejected(
            "dns_resolution_failed", "Hôte de destination impossible à résoudre"
        ) from exc
    if not addresses:
        raise OutboundURLRejected(
            "dns_resolution_failed", "Hôte de destination impossible à résoudre"
        )
    return addresses


def _reject_non_public_addresses(
    addresses: Iterable[ipaddress.IPv4Address | ipaddress.IPv6Address],
    *,
    allow_loopback: bool = False,
) -> None:
    for address in addresses:
        if allow_loopback and address.is_loopback:
            continue
        if not address.is_global:
            raise OutboundURLRejected(
                "non_public_address",
                "La destination résout vers une adresse privée ou non routable",
            )


def validate_public_https_url(
    value: str,
    *,
    allowed_hosts: str | Iterable[str],
    resolver: Callable[..., list[tuple]] | None = None,
) -> str:
    """Valide HTTPS/443, l'allowlist DNS et toutes les adresses résolues.

    Une destination n'est acceptée que si chaque adresse retournée est
    globalement routable. Une réponse DNS mixte public/privé est donc refusée.
    """
    candidate, host = _parse_https_destination(value)
    allowlist = _normalized_allowlist(allowed_hosts)
    if not allowlist or not _host_allowed(host, allowlist):
        raise OutboundURLRejected("host_not_allowed", "Hôte de destination non autorisé")
    addresses = _resolve_host_addresses(host, resolver=resolver)
    _reject_non_public_addresses(addresses)
    return candidate


def validate_open_world_https_url(
    value: str,
    *,
    resolver: Callable[..., list[tuple]] | None = None,
    allow_loopback: bool = False,
) -> str:
    """Valide HTTPS/443 public, sans catalogue d'hôtes.

    Le navigateur agentique n'a pas de liste d'hôtels : n'importe quel site
    HTTPS public peut être ouvert. Les adresses privées, metadata et
    identifiants dans l'URL restent refusés.
    """
    candidate, host = _parse_https_destination(value)
    addresses = _resolve_host_addresses(host, resolver=resolver)
    _reject_non_public_addresses(addresses, allow_loopback=allow_loopback)
    return candidate

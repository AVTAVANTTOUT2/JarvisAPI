"""Validation des destinations HTTP contrôlées par un client ou un fichier."""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


class OutboundURLRejected(ValueError):
    """Destination sortante refusée avant toute connexion réseau."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ResolvedPublicEndpoint:
    """Destination HTTPS normalisée et adresses validées avant connexion."""

    url: str
    host: str
    port: int
    addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]


_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_AMBIGUOUS_NUMERIC_HOST_RE = re.compile(
    r"(?i)^(?:0x[0-9a-f]+|0[0-7]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|0[0-7]+|[0-9]+))*$"
)
_LOCAL_HOST_SUFFIXES = (
    ".home",
    ".internal",
    ".invalid",
    ".lan",
    ".local",
    ".localdomain",
    ".localhost",
    ".onion",
    ".test",
)
_IPV6_EMBEDDED_IPV4_NETWORKS = (
    ipaddress.ip_network("::/96"),
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)


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


def _normalize_hostname(host: str) -> str:
    raw = host.strip().lower().rstrip(".")
    if not raw or len(raw) > 253 or "%" in raw:
        raise OutboundURLRejected("invalid_url", "Nom d'hôte invalide")
    try:
        literal = ipaddress.ip_address(raw.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if isinstance(literal, ipaddress.IPv6Address) and literal.ipv4_mapped is not None:
            raise OutboundURLRejected(
                "ambiguous_ip_address", "Adresse IPv4 mappée en IPv6 interdite"
            )
        return literal.compressed
    if _AMBIGUOUS_NUMERIC_HOST_RE.fullmatch(raw):
        raise OutboundURLRejected(
            "ambiguous_ip_address", "Représentation numérique d'adresse ambiguë"
        )
    try:
        normalized = raw.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise OutboundURLRejected("invalid_url", "Nom d'hôte invalide") from exc
    labels = normalized.split(".")
    if (
        len(labels) < 2
        or any(not _DNS_LABEL_RE.fullmatch(label) for label in labels)
        or normalized.endswith(_LOCAL_HOST_SUFFIXES)
    ):
        raise OutboundURLRejected(
            "local_hostname", "Nom d'hôte local ou non public interdit"
        )
    return normalized


def _parse_https_destination(value: str) -> tuple[str, str, int]:
    """Retourne ``(url, host)`` pour une destination HTTPS/443 sans secret."""

    candidate = str(value or "").strip()
    try:
        parsed = urlsplit(candidate)
        raw_host = parsed.hostname or ""
        port = parsed.port
    except ValueError as exc:
        raise OutboundURLRejected("invalid_url", "URL de destination invalide") from exc

    if (
        parsed.scheme.lower() != "https"
        or not raw_host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise OutboundURLRejected(
            "https_required",
            "La destination doit utiliser HTTPS sur le port 443, sans identifiants ni fragment",
        )
    host = _normalize_hostname(raw_host)
    # Authorisation and interception compare this canonical URL exactly.  Use
    # the same representation Chromium emits for a default HTTPS destination
    # (lower-case/IDNA host, no explicit :443, and an explicit root path).
    netloc = f"[{host}]" if ":" in host else host
    canonical = urlunsplit(
        ("https", netloc, parsed.path or "/", parsed.query, "")
    )
    return canonical, host, 443


def _resolve_host_addresses(
    host: str,
    *,
    port: int = 443,
    resolver: Callable[..., list[tuple]] | None = None,
) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        return {literal}
    except ValueError:
        pass
    try:
        lookup = resolver or socket.getaddrinfo
        answers = lookup(host, port, type=socket.SOCK_STREAM)
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
) -> None:
    for address in addresses:
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            raise OutboundURLRejected(
                "ambiguous_ip_address", "Adresse IPv4 mappée en IPv6 interdite"
            )
        if (
            isinstance(address, ipaddress.IPv6Address)
            and not address.is_unspecified
            and not address.is_loopback
            and (
                any(address in network for network in _IPV6_EMBEDDED_IPV4_NETWORKS)
                or address.sixtofour is not None
                or address.teredo is not None
            )
        ):
            raise OutboundURLRejected(
                "ambiguous_ip_address", "Adresse IPv4 encapsulée en IPv6 interdite"
            )
        if (
            not address.is_global
            or address.is_link_local
            or address.is_loopback
            or address.is_multicast
            or address.is_private
            or address.is_reserved
            or address.is_unspecified
        ):
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
    candidate, host, port = _parse_https_destination(value)
    allowlist = _normalized_allowlist(allowed_hosts)
    if not allowlist or not _host_allowed(host, allowlist):
        raise OutboundURLRejected("host_not_allowed", "Hôte de destination non autorisé")
    addresses = _resolve_host_addresses(host, port=port, resolver=resolver)
    _reject_non_public_addresses(addresses)
    return candidate


def resolve_open_world_https_url(
    value: str,
    *,
    resolver: Callable[..., list[tuple]] | None = None,
) -> ResolvedPublicEndpoint:
    """Résout et épingle logiquement une destination HTTPS publique.

    Le navigateur agentique n'a pas de liste d'hôtels : n'importe quel site
    HTTPS public peut être ouvert. Les adresses privées, metadata et
    identifiants dans l'URL restent refusés.
    """
    candidate, host, port = _parse_https_destination(value)
    addresses = _resolve_host_addresses(host, port=port, resolver=resolver)
    _reject_non_public_addresses(addresses)
    ordered = tuple(sorted(addresses, key=lambda item: (item.version, int(item))))
    return ResolvedPublicEndpoint(
        url=candidate,
        host=host,
        port=port,
        addresses=ordered,
    )


def canonicalize_open_world_https_url(value: str) -> str:
    """Canonicalise la syntaxe HTTPS publique sans effectuer de résolution DNS."""

    candidate, _host, _port = _parse_https_destination(value)
    return candidate


def validate_open_world_https_url(
    value: str,
    *,
    resolver: Callable[..., list[tuple]] | None = None,
) -> str:
    """Valide HTTPS/443 et toutes les adresses, sans catalogue d'hôtes."""

    return resolve_open_world_https_url(value, resolver=resolver).url

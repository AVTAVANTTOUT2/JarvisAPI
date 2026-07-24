"""Règles de sécurité communes aux points d'entrée réseau JARVIS."""

from __future__ import annotations

import ipaddress


def is_loopback_host(host: str) -> bool:
    """Indique si *host* limite réellement l'écoute à la machine locale."""
    normalized = host.strip().lower().strip("[]")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        # Un nom DNS ou une interface explicite peut être joignable du réseau.
        return False


def validate_network_bind(
    *,
    host: str,
    allow_network_bind: bool,
    https_enabled: bool,
    location_token: str,
) -> None:
    """Refuse une exposition réseau implicite ou entièrement non protégée."""
    if is_loopback_host(host):
        return
    if not allow_network_bind:
        raise RuntimeError(
            f"écoute réseau refusée sur {host!r}: définissez "
            "WEB_ALLOW_NETWORK_BIND=true pour l'autoriser explicitement"
        )
    if not https_enabled and not location_token.strip():
        raise RuntimeError(
            f"écoute réseau refusée sur {host!r}: activez WEB_HTTPS "
            "ou configurez LOCATION_API_TOKEN"
        )

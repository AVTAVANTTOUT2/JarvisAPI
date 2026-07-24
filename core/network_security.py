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
    https_behind_proxy: bool = False,
) -> None:
    """Refuse toute écoute réseau implicite ou transport HTTP distant."""
    if https_enabled and https_behind_proxy:
        raise RuntimeError(
            "WEB_HTTPS et WEB_HTTPS_BEHIND_PROXY sont mutuellement exclusifs"
        )
    if is_loopback_host(host):
        return
    if not allow_network_bind:
        raise RuntimeError(
            f"écoute réseau refusée sur {host!r}: définissez "
            "WEB_ALLOW_NETWORK_BIND=true pour l'autoriser explicitement"
        )
    if https_behind_proxy:
        raise RuntimeError(
            "WEB_HTTPS_BEHIND_PROXY exige un WEB_HOST loopback; "
            "le proxy TLS est le seul processus exposé au réseau"
        )
    if not https_enabled:
        raise RuntimeError(
            f"écoute HTTP refusée sur {host!r}: activez WEB_HTTPS avec "
            "un certificat valide, ou gardez WEB_HOST en loopback derrière "
            "un reverse proxy TLS"
        )

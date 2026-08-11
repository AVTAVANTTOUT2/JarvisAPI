"""Hiérarchie d'erreurs stable du SDK JARVIS."""

from __future__ import annotations

from typing import Any

from .models import JarvisResponse


class JarvisError(Exception):
    """Base de toutes les erreurs SDK attendues."""


class JarvisConfigurationError(JarvisError, ValueError):
    """Configuration locale invalide avant tout appel réseau."""


class JarvisAuthenticationError(JarvisError):
    """Credential requis absent ou incomplet."""


class JarvisTransportError(JarvisError):
    """Échec réseau, TLS, encodage ou réponse illisible."""


class JarvisResponseTooLarge(JarvisTransportError):
    """Réponse supérieure à la limite configurée."""


class JarvisApiError(JarvisError):
    """Réponse HTTP non 2xx avec détails structurés et bornés."""

    def __init__(
        self,
        status_code: int,
        code: str,
        detail: Any,
        response: JarvisResponse,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.response = response
        super().__init__(f"JARVIS API HTTP {status_code}: {code}")

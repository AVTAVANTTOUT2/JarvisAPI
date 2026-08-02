"""Accès contrôlé au runtime Playwright, importé uniquement à la demande.

Playwright est une dépendance lourde et facultative : rien dans JARVIS ne doit
échouer à l'import parce que le paquet ou les navigateurs ne sont pas
installés. Ce module centralise l'import tardif et l'exposition des types
d'erreur, pour que les appelants puissent écrire des ``except`` précis sans
importer Playwright au chargement du module.
"""

from __future__ import annotations

import logging
from types import ModuleType

logger = logging.getLogger("jarvis.playwright")

#: Nom du module d'API asynchrone de Playwright.
PLAYWRIGHT_ASYNC_MODULE = "playwright.async_api"


class PlaywrightUnavailable(RuntimeError):
    """Playwright n'est pas installé ou son import a échoué."""


def import_playwright() -> ModuleType:
    """Retourne ``playwright.async_api`` ou lève une erreur explicite.

    Raises:
        PlaywrightUnavailable: si le paquet est absent ou cassé.
    """
    try:
        import playwright.async_api as playwright_async_api
    except ImportError as exc:
        raise PlaywrightUnavailable(
            "Playwright absent : installer 'pip install playwright' puis "
            f"'playwright install chromium' (import de {PLAYWRIGHT_ASYNC_MODULE} : {exc})"
        ) from exc
    return playwright_async_api


def is_playwright_installed() -> bool:
    """Indique si le runtime Playwright est importable, sans lever d'erreur."""
    try:
        import_playwright()
    except PlaywrightUnavailable as exc:
        logger.debug("[playwright] runtime indisponible : %s", exc)
        return False
    return True


def playwright_errors() -> tuple[type[BaseException], ...]:
    """Types d'exception Playwright à capturer autour d'une interaction DOM.

    ``TimeoutError`` dérive de ``Error`` côté Playwright ; les deux sont
    retournés pour rester explicite et robuste à une évolution de hiérarchie.
    """
    api = import_playwright()
    return (api.TimeoutError, api.Error)

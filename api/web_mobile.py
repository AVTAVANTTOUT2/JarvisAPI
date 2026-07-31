"""Interface mobile autonome (``web_mobile/``) : montage et redirection.

HTML/CSS/JS vanilla, sans build ni dépendance npm. Servie sur la **même
origine** que l'API : le cookie ``jarvis_session`` (SameSite=Strict), la
vérification d'Origin du middleware et la CSP ``default-src 'self'``
fonctionnent alors sans aménagement.

Module séparé de :mod:`api.frontend` pour deux raisons : garder chaque module
``api/*`` sous 500 lignes, et matérialiser l'isolation de cette interface —
elle ne partage aucun code avec les frontends React.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

import config

BASE_DIR = Path(__file__).resolve().parent.parent
logger = logging.getLogger("jarvis")

WEB_MOBILE_DIR = Path(os.getenv("WEB_MOBILE_DIR", str(BASE_DIR / "web_mobile"))).resolve()
PREFIX = "/mobile"

# Échappatoire desktop : sans elle, un téléphone ne pourrait plus jamais
# atteindre l'interface complète, pas même pour déboguer.
FORCE_DESKTOP_COOKIE = "jarvis_force_desktop"

# Points d'entrée des deux manifests bureau qui ont existé. Une icône déjà
# installée sur l'iPhone ouvre directement l'un d'eux et ne passe donc jamais
# par ``/``. Ce cas de migration est distinct des autres liens profonds bureau.
LEGACY_PWA_ENTRYPOINTS = {
    "chat": "chat",
    "dashboard": "aujourdhui",
    "fitness": "sante",
}

# Règles de détection mobile — expression régulière compilée.
# Couvre : Android (hors tablettes), iOS (iPhone/iPod), Windows Phone,
# Opera Mini, BlackBerry, IEMobile, et le mot-clé générique "Mobile".
_MOBILE_UA_PATTERN = re.compile(
    r"(?:Android.*Mobile|iPhone|iPod|webOS|Windows\sPhone|Opera\sMini|"
    r"BlackBerry|IEMobile|Mobile[/;])",
    re.IGNORECASE,
)
# Une tablette Android n'a pas le mot « Mobile » dans son UA : écran large,
# on lui sert le bureau. L'iPad moderne s'annonce comme un Mac : idem.
_TABLET_UA_PATTERN = re.compile(r"Android(?!.*Mobile)", re.IGNORECASE)

# Types servis depuis web_mobile/. Toute extension absente est refusée : le
# répertoire ne doit exposer que du statique connu.
_MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


def is_mobile_device(user_agent: str) -> bool:
    """Détecte un téléphone via le User-Agent (tablettes exclues)."""
    if not user_agent:
        return False
    if _TABLET_UA_PATTERN.search(user_agent):
        return False
    return bool(_MOBILE_UA_PATTERN.search(user_agent))


def is_available() -> bool:
    """L'interface mobile autonome est-elle installée et servable ?"""
    return (WEB_MOBILE_DIR / "index.html").is_file()


def wants_desktop(request: Request) -> bool:
    """L'utilisateur a-t-il explicitement demandé l'interface bureau ?"""
    return (
        request.query_params.get("desktop") is not None
        or request.cookies.get(FORCE_DESKTOP_COOKIE) == "1"
    )


def should_redirect(request: Request) -> bool:
    """Faut-il rediriger cette requête racine vers l'interface mobile ?"""
    return (
        is_available()
        and not wants_desktop(request)
        and is_mobile_device(request.headers.get("user-agent", ""))
    )


def redirect() -> RedirectResponse:
    return RedirectResponse(f"{PREFIX}/", status_code=302)


def redirect_legacy_pwa_entry(request: Request, segment: str) -> RedirectResponse | None:
    """Migre une ancienne icône PWA bureau vers son écran mobile équivalent."""
    mobile_route = LEGACY_PWA_ENTRYPOINTS.get(segment)
    if not mobile_route or not should_redirect(request):
        return None
    return RedirectResponse(f"{PREFIX}/#/{mobile_route}", status_code=302)


def remember_desktop_choice(response: Response, request: Request) -> Response:
    """Fixe le cookie d'échappatoire quand ``?desktop=1`` est présent."""
    if request.query_params.get("desktop") is not None:
        response.set_cookie(
            FORCE_DESKTOP_COOKIE, "1",
            max_age=60 * 60 * 24 * 365, samesite="strict", path="/",
            secure=config.WEB_HTTPS or config.WEB_HTTPS_BEHIND_PROXY,
        )
    return response


def _serve(path: Path) -> FileResponse:
    media_type = _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    # Fichiers statiques versionnés à la main, sans hachage dans les noms :
    # pas de cache long, sinon une correction ne parviendrait jamais au client.
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-cache"})


def setup(app: FastAPI) -> bool:
    """Monte ``web_mobile/`` sous ``/mobile/``.

    À appeler **avant** le montage du frontend unifié : celui-ci enregistre un
    attrape-tout ``/{parent}/{child:path}`` puis retourne, ce qui masquerait
    ces routes et court-circuiterait la détection mobile.

    Returns:
        True si l'interface a été montée.
    """
    if not is_available():
        logger.info("web_mobile absent (%s) — pas de redirection mobile", WEB_MOBILE_DIR)
        return False

    index_file = WEB_MOBILE_DIR / "index.html"

    @app.get("/m", include_in_schema=False)
    @app.get("/m/", include_in_schema=False)
    async def redirect_legacy_mobile_root():
        """Migre l'ancien point d'entrée PWA vers l'interface autonome."""
        return RedirectResponse(f"{PREFIX}/", status_code=302)

    @app.get("/m/fitness", include_in_schema=False)
    @app.get("/m/fitness/", include_in_schema=False)
    async def redirect_legacy_mobile_fitness():
        """Conserve les favoris et icônes qui ciblaient l'ancien écran fitness."""
        return RedirectResponse(f"{PREFIX}/#/sante", status_code=302)

    @app.get(PREFIX, include_in_schema=False)
    async def serve_web_mobile_bare():
        return RedirectResponse(f"{PREFIX}/", status_code=307)

    @app.get(f"{PREFIX}/", include_in_schema=False)
    async def serve_web_mobile_root():
        return _serve(index_file)

    @app.get(f"{PREFIX}/{{asset:path}}", include_in_schema=False)
    async def serve_web_mobile_asset(asset: str):
        if not asset:
            return _serve(index_file)

        candidate = (WEB_MOBILE_DIR / asset).resolve()
        # Traversée de répertoire : le chemin résolu doit rester sous la racine.
        try:
            candidate.relative_to(WEB_MOBILE_DIR)
        except ValueError:
            raise HTTPException(404) from None

        if candidate.is_file() and candidate.suffix.lower() in _MEDIA_TYPES:
            return _serve(candidate)
        # Le routage se fait par fragment (#/chat) : aucune sous-route réelle
        # n'existe. Un fichier absent est un 404 franc, jamais l'index déguisé.
        raise HTTPException(404)

    logger.info("Interface mobile autonome montée sur %s/ (%s)", PREFIX, WEB_MOBILE_DIR)
    return True

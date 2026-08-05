"""Montage du frontend bureau Next.js et de l'interface mobile autonome."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api import web_mobile
from core.frontend_resolution import (
    is_usable_next_build,
    resolve_desktop_frontend_roots,
)
from core.html_security import secure_html_file_response

BASE_DIR = Path(__file__).resolve().parent.parent
logger = logging.getLogger("jarvis")


FRONTEND_DIST = Path(
    os.getenv("FRONTEND_DIST_DIR", str(BASE_DIR / "frontend" / "out"))
).resolve()

# Segments React Router (BrowserRouter) — liste blanche BIG BROTHER.
_SPA_SEGMENTS = frozenset({
    "chat", "voice", "tasks", "fitness", "food", "documents", "memory", "status",
    "dashboard", "contacts", "map", "analytics", "search", "data",
    "conversations", "calendar", "logs", "monitoring",
    "voice-debug", "control", "mission",
})


# `cognitive` manquait à l'union historique : /cognitive répondait 404 au
# rechargement dur alors que la page existait dans le build.
_UNIFIED_SEGMENTS = _SPA_SEGMENTS | {"mails", "config", "cognitive"}


# Détection UA : une seule implémentation, dans api/web_mobile.py.
_is_mobile_device = web_mobile.is_mobile_device


def _setup_unified_frontend(app: FastAPI) -> bool:
    """Monte le build Next.js 15 responsive lorsqu'il est disponible.

    Le frontend unifié est volontairement servi à la racine ; les téléphones
    n'y arrivent jamais, ils sont redirigés vers ``/mobile/`` en amont.
    Critère de validité partagé avec le supervisor : :func:`is_usable_next_build`.
    """
    if not is_usable_next_build(FRONTEND_DIST):
        if FRONTEND_DIST.exists() and not (FRONTEND_DIST / "_next" / "static").is_dir():
            logger.warning("Frontend unifié: _next/static absent — build incomplet")
        return False

    index_file = FRONTEND_DIST / "index.html"
    next_static = FRONTEND_DIST / "_next" / "static"

    app.mount(
        "/_next/static",
        StaticFiles(directory=str(next_static)),
        name="unified_next_static",
    )

    icons_dir = FRONTEND_DIST / "icons"
    if icons_dir.is_dir():
        app.mount("/icons", StaticFiles(directory=str(icons_dir)), name="unified_icons")

    for name, media_type in (
        ("manifest.webmanifest", "application/manifest+json"),
        ("sw.js", "application/javascript"),
    ):
        file_path = FRONTEND_DIST / name
        if not file_path.is_file():
            continue

        def _make_root_file_route(fp: Path, mt: str):
            async def _serve():
                return FileResponse(
                    fp,
                    media_type=mt,
                    headers={"Cache-Control": "no-cache"},
                )
            return _serve

        app.add_api_route(
            f"/{name}",
            _make_root_file_route(file_path, media_type),
            methods=["GET"],
            include_in_schema=False,
        )

    @app.get("/", include_in_schema=False)
    async def serve_unified_root(request: Request):
        if web_mobile.should_redirect(request):
            return web_mobile.redirect()
        return web_mobile.remember_desktop_choice(secure_html_file_response(
            index_file,
            headers={"Cache-Control": "no-cache"},
        ), request)

    @app.get("/{segment}", include_in_schema=False)
    async def serve_unified_segment(segment: str, request: Request):
        if segment not in _UNIFIED_SEGMENTS:
            raise HTTPException(404)
        legacy_redirect = web_mobile.redirect_legacy_pwa_entry(request, segment)
        if legacy_redirect:
            return legacy_redirect
        route_index = FRONTEND_DIST / segment / "index.html"
        return web_mobile.remember_desktop_choice(secure_html_file_response(
            route_index if route_index.is_file() else index_file,
            headers={"Cache-Control": "no-cache"},
        ), request)

    @app.get("/{parent}/{child:path}", include_in_schema=False)
    async def serve_unified_nested(parent: str, child: str):
        if parent in ("api", "_next", "icons", "static", "upload", "m", "mobile"):
            raise HTTPException(404)
        if parent not in _UNIFIED_SEGMENTS or child.startswith("api/"):
            raise HTTPException(404)
        return secure_html_file_response(
            index_file,
            headers={"Cache-Control": "no-cache"},
        )

    logger.info("Frontend unifié Next.js 15 : %s", FRONTEND_DIST)
    return True


def _setup_frontend(app: FastAPI) -> None:
    """Sert l'unique frontend bureau ou échoue explicitement s'il manque.

    L'interface mobile autonome est montée en premier : elle doit exister
    avant l'attrape-tout du frontend unifié, et sa redirection s'applique
    même si le build bureau manque.
    """
    # ── Interface mobile autonome ───────────────────────────────
    # Montée en premier : les routes /mobile/* doivent exister avant que
    # `_setup_unified_frontend` n'enregistre son attrape-tout `/{segment}`,
    # et avant son `return` qui court-circuite le reste de cette fonction.
    web_mobile.setup(app)

    # ── Frontend responsive unifié (unique runtime bureau) ──────
    # Même contrat que le supervisor (core.frontend_resolution).
    desktop = resolve_desktop_frontend_roots(
        FRONTEND_DIST,
        canonical_label="frontend/out",
    )
    if desktop.kind == "next_canonical" and _setup_unified_frontend(app):
        return

    # Aucun frontend bureau. L'interface mobile est autonome : elle ne doit pas
    # disparaître parce qu'un build React manque. Sans cette racine, un
    # téléphone recevrait 404 au lieu d'être redirigé vers /mobile/.
    @app.get("/", include_in_schema=False)
    async def serve_missing_desktop(request: Request):
        if web_mobile.should_redirect(request):
            return web_mobile.redirect()
        raise HTTPException(
            503,
            "Aucun frontend bureau : `cd frontend && pnpm install && pnpm build`. "
            "L'interface mobile reste disponible sur /mobile/ lorsqu'elle est installée.",
        )

    if web_mobile.is_available():
        logger.warning(
            "Aucun frontend bureau — seule l'interface mobile est servie (/mobile/)."
        )
        return

    logger.warning(
        "Aucun frontend bureau : `cd frontend && pnpm install && pnpm build`."
    )


# ── WebSocket broadcast (audio daemon → tous les clients) ────────────────────

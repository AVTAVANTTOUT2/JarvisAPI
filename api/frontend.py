"""Montage des frontends bureau (Next.js unifié, repli Vite, repli Jinja)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
from api import web_mobile
from core.frontend_resolution import (
    is_usable_next_build,
    is_usable_vite_build,
    resolve_desktop_frontend_roots,
)

BASE_DIR = Path(__file__).resolve().parent.parent
logger = logging.getLogger("jarvis")


FRONTEND_DIST = Path(
    os.getenv("FRONTEND_DIST_DIR", str(BASE_DIR / "frontend" / "out"))
).resolve()
WEB_DIST = Path(os.getenv("WEB_DIST_DIR", str(BASE_DIR / "web" / "dist"))).resolve()
WEB_STATIC = BASE_DIR / "web" / "static"
WEB_TEMPLATES = BASE_DIR / "web" / "templates"

# Segments React Router (BrowserRouter) — liste blanche BIG BROTHER.
_SPA_SEGMENTS = frozenset({
    "chat", "voice", "tasks", "fitness", "food", "documents", "memory", "status",
    "dashboard", "contacts", "map", "analytics", "search", "data",
    "conversations", "calendar", "logs", "monitoring",
    "voice-debug", "control", "mission", "mobile",
})


# `cognitive` manquait à l'union historique : /cognitive répondait 404 au
# rechargement dur alors que la page existait dans le build.
_UNIFIED_SEGMENTS = _SPA_SEGMENTS | {"mails", "config", "cognitive"}


# Détection UA : une seule implémentation, dans api/web_mobile.py.
_is_mobile_device = web_mobile.is_mobile_device


def _setup_unified_frontend(app: FastAPI) -> bool:
    """Monte le build Next.js 15 responsive lorsqu'il est disponible.

    L'ancien build Vite reste le fallback de :func:`_setup_frontend` et la
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
                return FileResponse(fp, media_type=mt, headers={"Cache-Control": "no-cache"})
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
        return web_mobile.remember_desktop_choice(FileResponse(
            index_file,
            media_type="text/html; charset=utf-8",
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
        return web_mobile.remember_desktop_choice(FileResponse(
            route_index if route_index.is_file() else index_file,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        ), request)

    @app.get("/{parent}/{child:path}", include_in_schema=False)
    async def serve_unified_nested(parent: str, child: str):
        if parent in ("api", "_next", "icons", "static", "upload", "m", "mobile"):
            raise HTTPException(404)
        if parent not in _UNIFIED_SEGMENTS or child.startswith("api/"):
            raise HTTPException(404)
        return FileResponse(
            index_file,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    logger.info("Frontend unifié Next.js 15 : %s", FRONTEND_DIST)
    return True


def _setup_frontend(app: FastAPI) -> None:
    """Sert le frontend unifié, puis Vite ou Jinja en repli.

    L'interface mobile autonome est montée en premier : elle doit exister
    avant l'attrape-tout du frontend unifié, et sa redirection s'applique
    quel que soit le repli bureau retenu ensuite.
    """
    if WEB_STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_STATIC), name="static")

    # ── Interface mobile autonome ───────────────────────────────
    # Montée en premier : les routes /mobile/* doivent exister avant que
    # `_setup_unified_frontend` n'enregistre son attrape-tout `/{segment}`,
    # et avant son `return` qui court-circuite le reste de cette fonction.
    web_mobile.setup(app)

    # ── Frontend responsive unifié (prioritaire) / Vite fallback ─
    # Même priorité que le supervisor (core.frontend_resolution).
    desktop = resolve_desktop_frontend_roots(
        FRONTEND_DIST,
        WEB_DIST,
        canonical_label="frontend/out",
        fallback_label="web/dist",
    )
    if desktop.kind == "next_canonical" and _setup_unified_frontend(app):
        return

    index_file = WEB_DIST / "index.html"
    if desktop.kind == "vite_fallback" and is_usable_vite_build(WEB_DIST):
        logger.info(
            "Canonical frontend missing: frontend/out — Using legacy Vite fallback: web/dist"
        )
        assets_dir = WEB_DIST / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="vite_assets")

        icons_dir = WEB_DIST / "icons"
        if icons_dir.is_dir():
            app.mount("/icons", StaticFiles(directory=icons_dir), name="vite_icons")

        # Fichiers PWA générés à la racine par vite-plugin-pwa — servis
        # explicitement (le Service Worker DOIT être à la racine "/sw.js"
        # pour contrôler toute l'app, il ne peut pas vivre sous /assets).
        for name, media_type in (
            ("manifest.webmanifest", "application/manifest+json"),
            ("sw.js", "application/javascript"),
            ("registerSW.js", "application/javascript"),
        ):
            file_path = WEB_DIST / name

            if not file_path.is_file():
                continue

            def _make_pwa_file_route(fp: Path, mt: str):
                async def _serve():
                    return FileResponse(
                        fp, media_type=mt,
                        headers={"Cache-Control": "no-cache"},
                    )
                return _serve

            app.add_api_route(
                f"/{name}", _make_pwa_file_route(file_path, media_type),
                methods=["GET"], include_in_schema=False,
            )

        @app.get("/", include_in_schema=False)
        async def serve_spa_root(request: Request):
            # Un téléphone ne doit jamais atteindre le repli bureau.
            if web_mobile.should_redirect(request):
                return web_mobile.redirect()

            try:
                return FileResponse(
                    index_file,
                    media_type="text/html; charset=utf-8",
                    content_disposition_type="inline",
                )
            except OSError as e:
                logger.error(f"SPA index inaccessible : {e}")
                raise HTTPException(503, "Fichiers frontend illisibles (permissions ou volume).") from e

        @app.get("/{segment}", include_in_schema=False)
        async def serve_spa_segment(segment: str, request: Request):
            if segment not in _SPA_SEGMENTS:
                raise HTTPException(404)
            legacy_redirect = web_mobile.redirect_legacy_pwa_entry(request, segment)
            if legacy_redirect:
                return legacy_redirect
            try:
                return web_mobile.remember_desktop_choice(FileResponse(
                    index_file,
                    media_type="text/html; charset=utf-8",
                    content_disposition_type="inline",
                ), request)
            except OSError as e:
                logger.error(f"SPA index inaccessible : {e}")
                raise HTTPException(503, "Fichiers frontend illisibles.") from e

        # Fallback SPA : routes imbriquees (/contacts/foo) sans extension fichier
        @app.get("/{parent}/{child:path}", include_in_schema=False)
        async def serve_spa_nested(parent: str, child: str):
            if parent in ("api", "assets", "static", "upload") or child.startswith("api/"):
                raise HTTPException(404)
            if parent not in _SPA_SEGMENTS:
                raise HTTPException(404)
            try:
                return FileResponse(
                    index_file,
                    media_type="text/html; charset=utf-8",
                    content_disposition_type="inline",
                )
            except OSError as e:
                logger.error(f"SPA nested inaccessible : {e}")
                raise HTTPException(503, "Fichiers frontend illisibles.") from e

        logger.info("Frontend React (Vite) : %s", WEB_DIST)
        return

    tmpl = WEB_TEMPLATES / "index.html"
    if tmpl.is_file():
        jinja = Jinja2Templates(directory=str(WEB_TEMPLATES))

        @app.get("/", response_class=HTMLResponse)
        async def serve_jinja(request: Request):
            if web_mobile.should_redirect(request):
                return web_mobile.redirect()
            return jinja.TemplateResponse(
                request,
                "index.html",
                {"request": request, "user_name": config.USER_NAME},
            )

        logger.info("Frontend legacy (Jinja) : %s", WEB_TEMPLATES)
        return

    # Aucun frontend bureau. L'interface mobile est autonome : elle ne doit pas
    # disparaître parce qu'un build React manque. Sans cette racine, un
    # téléphone recevrait 404 au lieu d'être redirigé vers /mobile/.
    if web_mobile.is_available():
        @app.get("/", include_in_schema=False)
        async def serve_root_mobile_only(request: Request):
            if web_mobile.should_redirect(request):
                return web_mobile.redirect()
            raise HTTPException(
                503,
                "Aucun frontend bureau : `cd frontend && pnpm install && pnpm build`. "
                "L'interface mobile reste disponible sur /mobile/.",
            )

        logger.warning(
            "Aucun frontend bureau — seule l'interface mobile est servie (/mobile/)."
        )
        return

    logger.warning(
        "Aucun frontend : `cd web && pnpm install && pnpm build`, "
        "ou restaurez web/templates/index.html."
    )


# ── WebSocket broadcast (audio daemon → tous les clients) ────────────────────

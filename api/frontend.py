"""Montage des frontends desktop, PWA et legacy."""

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

# Répertoire PWA mobile (build statique Next.js output: 'export')
PWA_DIR = Path(config.PWA_DIR).resolve() if config.PWA_DIR else None

# Segments React Router (BrowserRouter) — whitelist historique + BIG BROTHER.
_SPA_SEGMENTS = frozenset({
    "chat", "voice", "tasks", "documents", "memory", "status",
    "dashboard", "contacts", "map", "analytics", "search", "data",
    "conversations", "calendar", "logs", "monitoring",
    "voice-debug", "control", "mission", "mobile",
})

# Préfixe de la PWA (doit correspondre au basePath Next.js)
_PWA_PREFIX = "/m"


# Segments PWA : routes Next.js en export statique
_PWA_SEGMENTS = frozenset({
    "dashboard", "map", "mails", "tasks", "config", "voice",
})
_UNIFIED_SEGMENTS = _SPA_SEGMENTS | _PWA_SEGMENTS


# Détection UA : une seule implémentation, dans api/web_mobile.py.
_is_mobile_device = web_mobile.is_mobile_device


def _setup_pwa_frontend(app: FastAPI) -> bool:
    """Configure le serving de la PWA mobile (build statique Next.js).

    La PWA est servie sous le préfixe ``/m/`` pour partager la même origine
    HTTP que le backend — le cookie de session auth est donc automatiquement
    transmis sans configuration supplémentaire.

    Returns:
        True si la PWA a été montée avec succès.
    """
    if not PWA_DIR or not PWA_DIR.is_dir():
        return False

    pwa_index = PWA_DIR / "index.html"
    if not pwa_index.is_file():
        logger.warning("PWA: répertoire présent mais pas d'index.html — build manquant ?")
        return False

    # Assets Next.js (_next/static/)
    next_static = PWA_DIR / "_next" / "static"
    if next_static.is_dir():
        app.mount(
            f"{_PWA_PREFIX}/_next/static",
            StaticFiles(directory=str(next_static)),
            name="pwa_next_static",
        )
        logger.info("PWA: assets Next.js montés sur %s/_next/static", _PWA_PREFIX)
    else:
        logger.warning("PWA: _next/static absent — CSS/JS cassés sur mobile")

    # Icônes PWA
    pwa_icons = PWA_DIR / "icons"
    if pwa_icons.is_dir():
        app.mount(
            f"{_PWA_PREFIX}/icons",
            StaticFiles(directory=str(pwa_icons)),
            name="pwa_icons",
        )

    # Fichiers racine PWA : manifest.json, sw.js
    for filename in ("manifest.json", "sw.js", "workbox-4754cb34.js"):
        fp = PWA_DIR / filename
        if not fp.is_file():
            continue
        media_type = {
            "manifest.json": "application/manifest+json",
            "sw.js": "application/javascript",
            "workbox-4754cb34.js": "application/javascript",
        }.get(filename, "application/octet-stream")

        async def _serve_pwa_root(fp_local: Path = fp, mt: str = media_type):
            return FileResponse(
                fp_local, media_type=mt,
                headers={"Cache-Control": "public, max-age=3600"},
            )

        app.add_api_route(
            f"{_PWA_PREFIX}/{filename}",
            _serve_pwa_root,
            methods=["GET"],
            include_in_schema=False,
        )

    # Route racine PWA : /m/ et /m
    @app.get(f"{_PWA_PREFIX}/", include_in_schema=False)
    @app.get(f"{_PWA_PREFIX}", include_in_schema=False)
    async def serve_pwa_root():
        return FileResponse(
            pwa_index,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    # Routes PWA : /m/dashboard, /m/map, etc.
    @app.get(f"{_PWA_PREFIX}/{{segment}}", include_in_schema=False)
    async def serve_pwa_segment(segment: str):
        # Servir les fichiers statiques de la PWA
        candidate_html = PWA_DIR / f"{segment}.html"
        if candidate_html.is_file():
            return FileResponse(
                candidate_html,
                media_type="text/html; charset=utf-8",
                headers={"Cache-Control": "no-cache"},
            )
        # Fallback : SPA routing — servir l'index PWA
        if segment in _PWA_SEGMENTS:
            return FileResponse(
                pwa_index,
                media_type="text/html; charset=utf-8",
                headers={"Cache-Control": "no-cache"},
            )
        raise HTTPException(404)

    # Sous-routes PWA (ex: /m/tasks/create si existant)
    @app.get(f"{_PWA_PREFIX}/{{parent}}/{{child:path}}", include_in_schema=False)
    async def serve_pwa_nested(parent: str, child: str):
        if parent in ("_next", "icons"):
            raise HTTPException(404)
        return FileResponse(
            pwa_index,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    logger.info("PWA mobile montée sur %s (build: %s)", _PWA_PREFIX, PWA_DIR)
    return True


def _setup_unified_frontend(app: FastAPI) -> bool:
    """Monte le build Next.js 15 responsive lorsqu'il est disponible.

    L'ancien build Vite reste le fallback de :func:`_setup_frontend` et la
    PWA historique reste accessible sous ``/m/``. Le frontend unifié est
    volontairement servi à la racine : il choisit son layout côté client.
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
    async def serve_unified_segment(segment: str):
        if segment not in _UNIFIED_SEGMENTS:
            raise HTTPException(404)
        route_index = FRONTEND_DIST / segment / "index.html"
        return FileResponse(
            route_index if route_index.is_file() else index_file,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )

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
    """Sert le frontend unifié, puis Vite ou Jinja en fallback.

    Si la PWA mobile est configurée (``PWA_ENABLED=true``) et que le build
    statique Next.js est présent dans ``PWA_DIR``, elle est montée sous
    ``/m/`` et les terminaux mobiles sont automatiquement redirigés.
    """
    if WEB_STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_STATIC), name="static")

    # ── Interface mobile autonome ───────────────────────────────
    # Montée en premier : les routes /mobile/* doivent exister avant que
    # `_setup_unified_frontend` n'enregistre son attrape-tout `/{segment}`,
    # et avant son `return` qui court-circuite le reste de cette fonction.
    web_mobile.setup(app)

    # ── PWA mobile ──────────────────────────────────────────────
    pwa_available = False
    if config.PWA_ENABLED:
        pwa_available = _setup_pwa_frontend(app)

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
            # L'interface mobile autonome prime sur la PWA historique.
            if web_mobile.should_redirect(request):
                return web_mobile.redirect()

            # Redirection automatique mobile → PWA
            if pwa_available and _is_mobile_device(
                request.headers.get("user-agent", "")
            ):
                if config.PWA_URL:
                    # PWA externe (autre port/domaine) → redirection HTTP 302
                    return RedirectResponse(
                        config.PWA_URL, status_code=302,
                    )
                # PWA servie localement → redirection vers /m/
                return RedirectResponse(f"{_PWA_PREFIX}/", status_code=302)

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
            # Le préfixe PWA /m/ est déjà géré par _setup_pwa_frontend
            if segment == _PWA_PREFIX.lstrip("/"):
                raise HTTPException(404)

            if segment not in _SPA_SEGMENTS:
                raise HTTPException(404)
            try:
                return FileResponse(
                    index_file,
                    media_type="text/html; charset=utf-8",
                    content_disposition_type="inline",
                )
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

        logger.info(
            "Frontend React (Vite) : %s %s",
            WEB_DIST,
            "(+ PWA /m/)" if pwa_available else "",
        )
        return

    tmpl = WEB_TEMPLATES / "index.html"
    if tmpl.is_file():
        jinja = Jinja2Templates(directory=str(WEB_TEMPLATES))

        @app.get("/", response_class=HTMLResponse)
        async def serve_jinja(request: Request):
            return jinja.TemplateResponse(
                "index.html",
                {"request": request, "user_name": config.USER_NAME},
            )

        logger.info("Frontend legacy (Jinja) : %s", WEB_TEMPLATES)
        return

    logger.warning(
        "Aucun frontend : `cd web && pnpm install && pnpm build`, "
        "ou restaurez web/templates/index.html."
    )


# ── WebSocket broadcast (audio daemon → tous les clients) ────────────────────

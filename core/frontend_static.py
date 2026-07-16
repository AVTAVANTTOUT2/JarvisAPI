"""Montage HTTP du frontend desktop résolu (utilisé par le supervisor)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.frontend_resolution import (
    FALLBACK_REL,
    CANONICAL_REL,
    FrontendResolution,
    lookup_desktop_static_file,
)


MISSING_PAYLOAD = {
    "error": "frontend_build_missing",
    "message": "No desktop frontend build is available.",
    "expected": [CANONICAL_REL, FALLBACK_REL],
}


def register_desktop_frontend_routes(
    app: FastAPI,
    resolution: FrontendResolution,
) -> None:
    """Enregistre montages d'assets + catch-all frontend.

    Doit être appelé **après** les routes ``/api/*`` et WebSocket du supervisor.
    """
    if resolution.kind == "missing" or resolution.root is None:
        @app.get("/")
        async def frontend_missing_root():
            return JSONResponse(status_code=503, content=MISSING_PAYLOAD)

        @app.get("/{path:path}")
        async def frontend_missing_path(path: str):
            return JSONResponse(status_code=503, content=MISSING_PAYLOAD)

        return

    root = resolution.root

    if resolution.kind == "next_canonical":
        next_static = root / "_next" / "static"
        if next_static.is_dir():
            app.mount(
                "/_next/static",
                StaticFiles(directory=str(next_static)),
                name="supervisor_next_static",
            )
        icons = root / "icons"
        if icons.is_dir():
            app.mount("/icons", StaticFiles(directory=str(icons)), name="supervisor_icons")
    else:
        assets = root / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="supervisor_vite_assets")
        icons = root / "icons"
        if icons.is_dir():
            app.mount("/icons", StaticFiles(directory=str(icons)), name="supervisor_vite_icons")

    @app.get("/{path:path}", response_model=None)
    async def serve_desktop_frontend(path: str):
        target = lookup_desktop_static_file(resolution, path)
        if target is not None and target.is_file():
            # HTML jamais mis en cache : les chunks référencés changent à
            # chaque build (hash) et un index.html périmé casse l'app.
            headers = (
                {"Cache-Control": "no-cache"}
                if target.suffix in (".html", ".webmanifest", ".json") or target.name == "sw.js"
                else {"Cache-Control": "public, max-age=3600"}
            )
            return FileResponse(target, headers=headers)
        return JSONResponse(status_code=404, content={"error": "Page introuvable"})

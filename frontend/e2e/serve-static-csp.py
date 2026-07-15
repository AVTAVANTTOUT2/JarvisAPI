#!/usr/bin/env python3
"""Serve frontend/out avec les mêmes en-têtes CSP que FastAPI (régression page noire)."""

from __future__ import annotations

import http.server
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "frontend" / "out"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 3107

CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "img-src 'self' data: blob: https://*.tile.openstreetmap.org; "
    "media-src 'self' blob:; "
    "connect-src 'self' ws: wss:; "
    "worker-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


if __name__ == "__main__":
    if not OUT.is_dir():
        sys.stderr.write(f"Build manquant: {OUT}\n")
        sys.exit(1)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Serving {OUT} on http://127.0.0.1:{PORT} with FastAPI-like CSP", flush=True)
    server.serve_forever()

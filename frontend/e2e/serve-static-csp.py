#!/usr/bin/env python3
"""Serve frontend/out avec les mêmes en-têtes CSP que FastAPI (régression page noire)."""

from __future__ import annotations

import http.server
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "frontend" / "out"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 3107

sys.path.insert(0, str(ROOT))

from security_headers import (  # noqa: E402
    CONTENT_SECURITY_POLICY,
    content_security_policy_for_html,
)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUT), **kwargs)

    def end_headers(self) -> None:
        policy = CONTENT_SECURITY_POLICY
        request_path = self.path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
        target = (OUT / (request_path or "index.html")).resolve()
        try:
            target.relative_to(OUT.resolve())
        except ValueError:
            target = OUT / "__invalid__"
        if target.is_dir():
            target = target / "index.html"
        if target.suffix == ".html" and target.is_file():
            policy = content_security_policy_for_html(target.read_bytes())
        self.send_header("Content-Security-Policy", policy)
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


if __name__ == "__main__":
    if not OUT.is_dir():
        sys.stderr.write(f"Build manquant: {OUT}\n")
        sys.exit(1)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Serving {OUT} on http://127.0.0.1:{PORT} with FastAPI-like CSP", flush=True)
    server.serve_forever()

"""Contrats de routage du frontend unifié et de ses fallbacks."""

import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.frontend as frontend


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_unified_frontend_is_prioritized_and_routes_are_static(tmp_path, monkeypatch):
    unified = tmp_path / "frontend"
    _write(unified / "index.html", "unified-root")
    _write(unified / "dashboard" / "index.html", "unified-dashboard")
    _write(unified / "_next" / "static" / "app.js", "asset")
    _write(unified / "manifest.webmanifest", "{}")

    monkeypatch.setattr(frontend, "FRONTEND_DIST", unified)
    monkeypatch.setattr(frontend, "WEB_DIST", tmp_path / "missing-web")
    monkeypatch.setattr(frontend, "PWA_DIR", None)
    monkeypatch.setattr(frontend.config, "PWA_ENABLED", False)

    app = FastAPI()
    frontend._setup_frontend(app)

    with TestClient(app) as client:
        assert client.get("/").text == "unified-root"
        assert client.get("/dashboard").text == "unified-dashboard"
        assert client.get("/_next/static/app.js").text == "asset"
        assert client.get("/unknown").status_code == 404


def test_historical_pwa_coexists_with_unified_frontend(tmp_path, monkeypatch):
    unified = tmp_path / "frontend"
    pwa = tmp_path / "pwa"
    _write(unified / "index.html", "unified-root")
    _write(unified / "_next" / "static" / "app.js", "asset")
    _write(pwa / "index.html", "historical-pwa")

    monkeypatch.setattr(frontend, "FRONTEND_DIST", unified)
    monkeypatch.setattr(frontend, "PWA_DIR", pwa)
    monkeypatch.setattr(frontend.config, "PWA_ENABLED", True)

    app = FastAPI()
    frontend._setup_frontend(app)

    with TestClient(app) as client:
        assert client.get("/").text == "unified-root"
        assert client.get("/m/").text == "historical-pwa"


def test_vite_frontend_remains_fallback_without_unified_build(tmp_path, monkeypatch):
    web = tmp_path / "web"
    _write(web / "index.html", "vite-fallback")

    monkeypatch.setattr(frontend, "FRONTEND_DIST", tmp_path / "missing-unified")
    monkeypatch.setattr(frontend, "WEB_DIST", web)
    monkeypatch.setattr(frontend, "PWA_DIR", None)
    monkeypatch.setattr(frontend.config, "PWA_ENABLED", False)

    app = FastAPI()
    frontend._setup_frontend(app)

    with TestClient(app) as client:
        assert client.get("/").text == "vite-fallback"


def test_desktop_and_mobile_share_one_authenticated_api_wrapper():
    assert not (REPO_ROOT / "web/src/services/api.ts").exists()
    assert not (REPO_ROOT / "pwa/src/lib/api.ts").exists()

    direct_fetches = []
    for source_root in ("web/src", "pwa/src", "frontend/src"):
        for path in (REPO_ROOT / source_root).rglob("*"):
            if path.suffix not in {".ts", ".tsx"} or path.name.endswith(".test.ts"):
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"(?<![.`])\bfetch\(", line) and not line.lstrip().startswith("*"):
                    direct_fetches.append((path.relative_to(REPO_ROOT).as_posix(), line_number))

    assert [path for path, _ in direct_fetches] == ["frontend/src/lib/api.ts"]
    api_source = (REPO_ROOT / "frontend/src/lib/api.ts").read_text(encoding="utf-8")
    assert "credentials: 'include'" in api_source
    assert "@unified/lib/api" in (REPO_ROOT / "web/src/pages/MissionControl.tsx").read_text()
    assert "@unified/lib/api" in (REPO_ROOT / "pwa/src/lib/geolocation.ts").read_text()
    pwa_layout = (REPO_ROOT / "pwa/src/app/client-layout.tsx").read_text(encoding="utf-8")
    assert "<LockGate onAuthenticated={startAuthenticatedTracking}>" in pwa_layout

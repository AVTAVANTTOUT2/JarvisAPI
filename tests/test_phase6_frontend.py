"""Contrats de routage de l'unique frontend bureau.

L'interface mobile ne figure plus ici : elle est autonome et couverte par
``tests/test_web_mobile.py``. C'est précisément le but — casser le mobile ne
doit plus pouvoir casser le bureau, ni l'inverse.
"""

import json
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import frontend

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_unified_frontend_is_prioritized_and_routes_are_static(tmp_path, monkeypatch):
    unified = tmp_path / "frontend"
    _write(unified / "index.html", "unified-root")
    _write(unified / "dashboard" / "index.html", "unified-dashboard")
    _write(unified / "voice-display" / "index.html", "voice-display")
    _write(unified / "_next" / "static" / "app.js", "asset")
    _write(unified / "manifest.webmanifest", "{}")

    monkeypatch.setattr(frontend, "FRONTEND_DIST", unified)
    app = FastAPI()
    frontend._setup_frontend(app)

    with TestClient(app) as client:
        assert client.get("/").text == "unified-root"
        assert client.get("/dashboard").text == "unified-dashboard"
        assert client.get("/voice-display").text == "voice-display"
        assert client.get("/_next/static/app.js").text == "asset"
        assert client.get("/unknown").status_code == 404


def test_cognitive_route_is_served(tmp_path, monkeypatch):
    """`cognitive` manquait à la liste blanche : 404 au rechargement dur."""
    unified = tmp_path / "frontend"
    _write(unified / "index.html", "unified-root")
    _write(unified / "cognitive" / "index.html", "unified-cognitive")
    _write(unified / "_next" / "static" / "app.js", "asset")

    monkeypatch.setattr(frontend, "FRONTEND_DIST", unified)
    app = FastAPI()
    frontend._setup_frontend(app)

    with TestClient(app) as client:
        assert client.get("/cognitive").text == "unified-cognitive"


def test_food_route_is_served(tmp_path, monkeypatch):
    """La page Nourriture exportée doit survivre à un rechargement direct.

    Sans son segment dans la liste blanche serveur, `/food` répondrait 404 au
    rechargement dur alors que la page existe bien dans le build.
    """
    unified = tmp_path / "frontend"
    _write(unified / "index.html", "unified-root")
    _write(unified / "food" / "index.html", "unified-food")
    _write(unified / "_next" / "static" / "app.js", "asset")

    monkeypatch.setattr(frontend, "FRONTEND_DIST", unified)
    app = FastAPI()
    frontend._setup_frontend(app)

    with TestClient(app) as client:
        assert client.get("/food").text == "unified-food"


def test_fitness_route_is_served(tmp_path, monkeypatch):
    """La page Fitness exportée doit survivre à un rechargement direct."""
    unified = tmp_path / "frontend"
    _write(unified / "index.html", "unified-root")
    _write(unified / "fitness" / "index.html", "unified-fitness")
    _write(unified / "_next" / "static" / "app.js", "asset")

    monkeypatch.setattr(frontend, "FRONTEND_DIST", unified)
    app = FastAPI()
    frontend._setup_frontend(app)

    with TestClient(app) as client:
        assert client.get("/fitness").text == "unified-fitness"


def test_retired_vite_build_is_not_a_runtime_fallback(tmp_path, monkeypatch):
    web = tmp_path / "web"
    _write(web / "index.html", "retired-vite-build")

    monkeypatch.setattr(frontend, "FRONTEND_DIST", tmp_path / "missing-unified")

    app = FastAPI()
    frontend._setup_frontend(app)

    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 503
        assert "retired-vite-build" not in response.text


def test_the_historical_pwa_mount_redirects_without_being_served(tmp_path, monkeypatch):
    """L'ancien /m/ migre vers /mobile/ sans restaurer le build PWA supprimé."""
    unified = tmp_path / "frontend"
    _write(unified / "index.html", "unified-root")
    _write(unified / "_next" / "static" / "app.js", "asset")

    monkeypatch.setattr(frontend, "FRONTEND_DIST", unified)
    app = FastAPI()
    frontend._setup_frontend(app)

    with TestClient(app) as client:
        response = client.get("/m/", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/mobile/"
    assert not hasattr(frontend, "_setup_pwa_frontend")


def test_desktop_uses_one_authenticated_api_wrapper():
    assert not (REPO_ROOT / "web/src/services/api.ts").exists()

    direct_fetches = []
    for source_root in ("web/src", "frontend/src"):
        for path in (REPO_ROOT / source_root).rglob("*"):
            if path.suffix not in {".ts", ".tsx"} or path.name.endswith(".test.ts"):
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"(?<![.`])\bfetch\(", line) and not line.lstrip().startswith("*"):
                    direct_fetches.append((path.relative_to(REPO_ROOT).as_posix(), line_number))

    assert [path for path, _ in direct_fetches] == ["frontend/src/lib/http.ts"]
    http_source = (REPO_ROOT / "frontend/src/lib/http.ts").read_text(encoding="utf-8")
    api_source = (REPO_ROOT / "frontend/src/lib/api.ts").read_text(encoding="utf-8")
    assert "credentials: 'include'" in http_source
    assert "jarvisRawFetch" in api_source
    assert "@unified/lib/api" in (REPO_ROOT / "web/src/pages/MissionControl.tsx").read_text()


def test_unified_bundle_no_longer_compiles_a_mobile_layout():
    """Le mobile ne doit plus entrer dans le bundle bureau."""
    assert not (REPO_ROOT / "frontend/src/components/MobileApp.tsx").exists()
    for config_file in ("frontend/next.config.js", "frontend/tsconfig.json",
                        "frontend/vitest.config.ts", "frontend/src/app/globals.css"):
        text = (REPO_ROOT / config_file).read_text(encoding="utf-8")
        assert "@mobile" not in text
        assert "pwa/src" not in text


def test_desktop_views_are_not_a_second_executable_app():
    package = json.loads((REPO_ROOT / "web/package.json").read_text(encoding="utf-8"))
    scripts = package.get("scripts", {})
    dependencies = {
        **package.get("dependencies", {}),
        **package.get("devDependencies", {}),
    }

    assert "dev" not in scripts
    assert "build" not in scripts
    assert "preview" not in scripts
    assert "vite" not in dependencies
    assert "vite-plugin-pwa" not in dependencies
    for retired_entry in (
        "web/index.html",
        "web/vite.config.ts",
        "web/src/main.tsx",
        "web/src/sw.ts",
    ):
        assert not (REPO_ROOT / retired_entry).exists()

    runtime_sources = (
        "api/frontend.py",
        "core/frontend_resolution.py",
        "core/frontend_static.py",
        "supervisor.py",
        "web/src/app/components/views/ControlView.tsx",
    )
    for source in runtime_sources:
        text = (REPO_ROOT / source).read_text(encoding="utf-8")
        assert "vite_fallback" not in text
        assert "vite_dev" not in text
        assert "Vite Dev" not in text
        assert "web/dist" not in text

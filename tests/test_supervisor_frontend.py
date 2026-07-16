"""Tests HTTP du montage frontend supervisor (sans démarrer le vrai processus)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.frontend_resolution import resolve_desktop_frontend
from core.frontend_static import register_desktop_frontend_routes
from supervisor import _build_proxy_headers, proxy_to_backend


def _make_next(root: Path) -> None:
    out = root / "frontend" / "out"
    (out / "_next" / "static" / "chunks").mkdir(parents=True)
    (out / "chat").mkdir(parents=True)
    (out / "dashboard").mkdir(parents=True)
    (out / "index.html").write_text("<html>NEXT</html>", encoding="utf-8")
    (out / "chat" / "index.html").write_text("<html>CHAT</html>", encoding="utf-8")
    (out / "dashboard" / "index.html").write_text("<html>DASH</html>", encoding="utf-8")
    (out / "_next" / "static" / "chunks" / "main.js").write_text("/* next */", encoding="utf-8")


def _make_vite(root: Path) -> None:
    dist = root / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>VITE</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("/* vite */", encoding="utf-8")


def _app_with_frontend(repo: Path) -> FastAPI:
    app = FastAPI()

    @app.get("/api/supervisor/status")
    async def status():
        res = resolve_desktop_frontend(repo)
        return {"frontend": res.to_public_dict()}

    @app.get("/api/ping")
    async def ping():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    register_desktop_frontend_routes(app, resolve_desktop_frontend(repo))
    return app


def test_http_next_root_and_routes(tmp_path: Path) -> None:
    _make_next(tmp_path)
    client = TestClient(_app_with_frontend(tmp_path))
    r = client.get("/")
    assert r.status_code == 200
    assert "NEXT" in r.text
    r = client.get("/chat")
    assert r.status_code == 200
    assert "CHAT" in r.text
    r = client.get("/chat/")
    assert r.status_code == 200
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "DASH" in r.text
    r = client.get("/_next/static/chunks/main.js")
    assert r.status_code == 200
    assert "next" in r.text
    r = client.get("/_next/static/missing.js")
    assert r.status_code == 404
    r = client.get("/unknown-page")
    assert r.status_code == 404
    r = client.get("/favicon.ico")
    assert r.status_code == 404


def test_http_vite_fallback(tmp_path: Path) -> None:
    _make_vite(tmp_path)
    client = TestClient(_app_with_frontend(tmp_path))
    assert "VITE" in client.get("/").text
    assert client.get("/chat").status_code == 200
    assert "VITE" in client.get("/chat").text
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/assets/nope.js").status_code == 404


def test_http_missing_frontend_explicit_error(tmp_path: Path) -> None:
    client = TestClient(_app_with_frontend(tmp_path))
    r = client.get("/")
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "frontend_build_missing"
    assert "frontend/out" in body["expected"]
    assert "web/dist" in body["expected"]
    # Santé / API toujours OK
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/ping").json()["ok"] is True


def test_http_diagnostic_frontend(tmp_path: Path) -> None:
    _make_next(tmp_path)
    _make_vite(tmp_path)
    client = TestClient(_app_with_frontend(tmp_path))
    data = client.get("/api/supervisor/status").json()
    assert data["frontend"]["selected"] == "next_canonical"
    assert data["frontend"]["path"] == "frontend/out"
    assert data["frontend"]["canonical_available"] is True
    assert data["frontend"]["fallback_available"] is True
    # Pas de chemin absolu utilisateur
    assert data["frontend"]["path"].startswith("frontend/")
    assert "/Users/" not in str(data["frontend"])


def test_api_not_intercepted_by_frontend(tmp_path: Path) -> None:
    _make_next(tmp_path)
    client = TestClient(_app_with_frontend(tmp_path))
    assert client.get("/api/ping").json() == {"ok": True}


def test_real_checkout_integration() -> None:
    """Si des builds réels existent, vérifie la sélection et un échantillon HTTP."""
    repo = Path(__file__).resolve().parents[1]
    res = resolve_desktop_frontend(repo)
    client = TestClient(_app_with_frontend(repo))

    status = client.get("/api/supervisor/status").json()["frontend"]
    assert status["selected"] == res.kind

    if res.kind == "next_canonical":
        home = client.get("/")
        assert home.status_code == 200
        assert "html" in home.headers.get("content-type", "").lower()
        # Asset Next réellement présent
        static = repo / "frontend" / "out" / "_next" / "static"
        js_files = list(static.rglob("*.js"))
        assert js_files, "frontend/out présent mais sans JS — build incomplet"
        rel = js_files[0].relative_to(repo / "frontend" / "out")
        asset = client.get(f"/{rel.as_posix()}")
        assert asset.status_code == 200
        chat = client.get("/chat")
        assert chat.status_code == 200
    elif res.kind == "vite_fallback":
        assert client.get("/").status_code == 200
    else:
        assert client.get("/").status_code == 503


def test_html_and_sw_are_no_cache(tmp_path: Path) -> None:
    _make_next(tmp_path)
    out = tmp_path / "frontend" / "out"
    (out / "sw.js").write_text("// sw", encoding="utf-8")
    (out / "manifest.webmanifest").write_text("{}", encoding="utf-8")
    client = TestClient(_app_with_frontend(tmp_path))
    assert client.get("/").headers.get("cache-control") == "no-cache"
    assert client.get("/chat").headers.get("cache-control") == "no-cache"
    assert client.get("/sw.js").headers.get("cache-control") == "no-cache"
    assert client.get("/manifest.webmanifest").headers.get("cache-control") == "no-cache"


def test_proxy_preserves_host_origin_cookie() -> None:
    """Non-régression VAL-02 : Host/Origin/Cookie transmis au backend."""
    headers = _build_proxy_headers({
        "Host": "localhost:9000",
        "Origin": "http://localhost:9000",
        "Cookie": "jarvis_session=test-token",
        "Content-Length": "4",
        "Connection": "keep-alive",
        "Transfer-Encoding": "chunked",
        "Accept": "application/json",
    })
    assert headers["Host"] == "localhost:9000"
    assert headers["Origin"] == "http://localhost:9000"
    assert headers["Cookie"] == "jarvis_session=test-token"
    assert "Content-Length" not in headers
    assert "Connection" not in headers
    assert "Transfer-Encoding" not in headers


def test_proxy_closes_stream_when_read_fails() -> None:
    """Réponse httpx streamée toujours fermée si aread() échoue (fuite pool)."""

    class FakeReq:
        method = "GET"
        headers = {
            "host": "localhost:9000",
            "origin": "http://localhost:9000",
            "cookie": "jarvis_session=x",
            "accept": "application/json",
        }

        class url:
            query = ""

        async def body(self) -> bytes:
            return b""

    closed = {"n": 0}
    resp = MagicMock()
    resp.headers = {"content-type": "application/json"}
    resp.status_code = 200

    async def _aread() -> bytes:
        raise RuntimeError("boom-read")

    async def _aclose() -> None:
        closed["n"] += 1

    resp.aread = _aread
    resp.aclose = _aclose

    with (
        patch("supervisor._port_open", return_value=True),
        patch("supervisor._http") as http,
    ):
        http.build_request = MagicMock(return_value=MagicMock(extensions={}))
        http.send = AsyncMock(return_value=resp)
        out = asyncio.run(proxy_to_backend(FakeReq(), "tasks"))  # type: ignore[arg-type]

    assert out.status_code == 502
    assert closed["n"] == 1


def test_websockets_connect_accepts_additional_headers() -> None:
    """Compat websockets 15+ : l'API utilisée par /ws expose additional_headers."""
    import inspect

    import websockets

    sig = inspect.signature(websockets.connect)
    assert "additional_headers" in sig.parameters
    assert websockets.__version__.startswith("15.")

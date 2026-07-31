from __future__ import annotations

import ipaddress
import json
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from scripts import tv_mcp_server
from tv import config as tv_config
from tv import server as tv_server


TEST_TOKEN = "test-tv-token-with-at-least-32-bytes"


@pytest.fixture
def secured_tv(monkeypatch: pytest.MonkeyPatch):
    async def healthy_backend():
        return {"alive": True, "data": {"status": "ok"}}

    monkeypatch.setattr(tv_server.cfg, "TV_AUTH_TOKEN", TEST_TOKEN)
    monkeypatch.setattr(tv_server.cfg, "TV_COOKIE_SECURE", False)
    monkeypatch.setattr(
        tv_server,
        "WHITELIST",
        [ipaddress.ip_network("127.0.0.0/8")],
    )
    monkeypatch.setattr(tv_server, "TRUSTED_PROXIES", [])
    monkeypatch.setattr(tv_server, "_get_client_ip", lambda request: "127.0.0.1")
    monkeypatch.setattr(tv_server, "_check_backend_health", healthy_backend)
    return tv_server


def _client() -> TestClient:
    return TestClient(tv_server.app)


@pytest.mark.parametrize(
    "path",
    ["/", "/api/messages", "/api/events", "/static/js/tv-v2.js"],
)
def test_anonymous_access_is_refused_everywhere(secured_tv, path: str):
    response = _client().get(path)

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_untrusted_x_forwarded_for_cannot_spoof_loopback(monkeypatch):
    monkeypatch.setattr(tv_server, "TRUSTED_PROXIES", [])
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", b"127.0.0.1")],
        "client": ("203.0.113.40", 50000),
        "server": ("192.0.2.10", 5174),
    }

    assert tv_server._get_client_ip(Request(scope)) == "203.0.113.40"


def test_x_forwarded_for_is_used_only_for_declared_proxy(monkeypatch):
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", b"198.51.100.12")],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 5174),
    }
    request = Request(scope)

    assert tv_server._get_client_ip(request) == "127.0.0.1"

    monkeypatch.setattr(
        tv_server,
        "TRUSTED_PROXIES",
        [ipaddress.ip_network("127.0.0.0/8")],
    )
    assert tv_server._get_client_ip(request) == "198.51.100.12"


def test_uvicorn_does_not_preprocess_forwarded_headers(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(tv_server.cfg, "TV_AUTH_TOKEN", TEST_TOKEN)
    monkeypatch.setattr(tv_server.cfg, "TV_HOST", "127.0.0.1")
    monkeypatch.setattr(tv_server.cfg, "TV_ALLOW_NETWORK_BIND", False)
    monkeypatch.setattr(tv_server.uvicorn, "run", lambda *args, **kwargs: captured.update(kwargs))

    tv_server.main()

    assert captured["proxy_headers"] is False


def test_invalid_token_is_refused(secured_tv):
    response = _client().get("/", headers={"Authorization": "Bearer invalid"})

    assert response.status_code == 401


def test_authenticated_dashboard_and_cookie_bootstrap_still_work(secured_tv):
    client = _client()

    bearer_response = client.get(
        "/",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
    )
    assert bearer_response.status_code == 200
    assert "JARVIS" in bearer_response.text

    bootstrap = client.get(f"/?token={TEST_TOKEN}", follow_redirects=False)
    assert bootstrap.status_code == 303
    assert bootstrap.headers["location"] == "/"
    assert "HttpOnly" in bootstrap.headers["set-cookie"]
    assert "SameSite=strict" in bootstrap.headers["set-cookie"]

    cookie_response = client.get("/")
    assert cookie_response.status_code == 200

    api_response = client.get("/api/health")
    assert api_response.status_code == 200


def test_tv_server_network_configuration_fails_closed():
    with pytest.raises(RuntimeError, match="TV_AUTH_TOKEN"):
        tv_config.validate_security_config(
            host="127.0.0.1",
            auth_token="",
            allow_network_bind=False,
        )

    with pytest.raises(RuntimeError, match="Bind TV réseau refusé"):
        tv_config.validate_security_config(
            host="0.0.0.0",
            auth_token=TEST_TOKEN,
            allow_network_bind=False,
        )

    tv_config.validate_security_config(
        host="0.0.0.0",
        auth_token=TEST_TOKEN,
        allow_network_bind=True,
    )


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(document.domain)",
        "file:///etc/passwd",
        "https://arbitrary.example/",
    ],
)
@pytest.mark.asyncio
async def test_tv_navigate_rejects_unsafe_urls_before_adb(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
):
    ready_called = False

    async def fake_ensure_ready() -> bool:
        nonlocal ready_called
        ready_called = True
        return True

    monkeypatch.setattr(
        tv_mcp_server,
        "ALLOWED_NAVIGATION_HOSTS",
        frozenset({"dashboard.local:5174"}),
    )
    monkeypatch.setattr(tv_mcp_server.tv_browser, "ensure_ready", fake_ensure_ready)

    response = await tv_mcp_server.handle_mcp_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "tv_navigate", "arguments": {"url": url}},
        }
    )
    payload = json.loads(response["result"]["content"][0]["text"])

    assert payload["ok"] is False
    assert ready_called is False


@pytest.mark.asyncio
async def test_tv_press_key_rejects_arbitrary_keycode_before_adb(
    monkeypatch: pytest.MonkeyPatch,
):
    ready_called = False

    async def fake_ensure_ready() -> bool:
        nonlocal ready_called
        ready_called = True
        return True

    monkeypatch.setattr(tv_mcp_server.tv_browser, "ensure_ready", fake_ensure_ready)

    response = await tv_mcp_server.handle_mcp_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "tv_press_key",
                "arguments": {"key": "KEYCODE_POWER"},
            },
        }
    )
    payload = json.loads(response["result"]["content"][0]["text"])

    assert payload == {"ok": False, "error": "Touche non autorisée"}
    assert ready_called is False


@pytest.mark.asyncio
async def test_network_adb_configuration_is_refused_without_opt_in(monkeypatch):
    assert tv_mcp_server.adb_configuration_error("192.0.2.10:5555", False)

    async def unexpected_command(*args, **kwargs):
        pytest.fail("aucune commande ADB ne doit être lancée")

    monkeypatch.setattr(tv_mcp_server, "ADB_TARGET", "192.0.2.10:5555")
    monkeypatch.setattr(tv_mcp_server, "TV_ALLOW_NETWORK_ADB", False)
    monkeypatch.setattr(tv_mcp_server, "run_cmd", unexpected_command)

    assert await tv_mcp_server.TVBrowser().ensure_ready() is False


def test_tv_browser_launch_agent_is_not_automatic():
    plist = (Path(__file__).parents[1] / "tv" / "com.jarvis.tv-browser.plist").read_text()

    assert "<key>RunAtLoad</key>\n    <false/>" in plist

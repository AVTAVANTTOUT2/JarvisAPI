"""Régressions d'authentification des control planes supervisor/backend."""

from __future__ import annotations

import asyncio
import stat
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.conftest import TEST_AUTH_SECRET


@pytest.fixture
def control_plane_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    import config
    import database

    db_path = tmp_path / "control-plane.db"
    token_path = tmp_path / ".supervisor-control-token"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(
        config,
        "SUPERVISOR_CONTROL_TOKEN_FILE",
        token_path,
        raising=False,
    )
    database.init_db()
    return token_path


async def _backend_request(
    method: str,
    path: str,
    *,
    headers: dict[str, str],
    client_host: str,
    cookies: dict[str, str] | None = None,
) -> httpx.Response:
    import main

    transport = httpx.ASGITransport(
        app=main.app,
        client=(client_host, 54321),
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://backend.test",
    ) as client:
        if cookies:
            client.cookies.update(cookies)
        return await client.request(method, path, headers=headers)


def test_legacy_supervisor_header_no_longer_bypasses_backend_auth(control_plane_db):
    import auth

    auth.setup_secret(TEST_AUTH_SECRET)
    with patch("api.router_daemon._get_all_services_status", return_value=[]):
        response = asyncio.run(
            _backend_request(
                "GET",
                "/api/control/services",
                headers={"X-Jarvis-Supervisor": "1"},
                client_host="127.0.0.1",
            )
        )

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_private_control_token_authenticates_loopback_backend_call(control_plane_db):
    from core.supervisor_auth import supervisor_control_headers

    headers = supervisor_control_headers()
    with patch("api.router_daemon._get_all_services_status", return_value=[]):
        response = asyncio.run(
            _backend_request(
                "GET",
                "/api/control/services",
                headers=headers,
                client_host="127.0.0.1",
            )
        )

    assert response.status_code == 200
    assert response.json() == {"services": []}
    assert stat.S_IMODE(control_plane_db.stat().st_mode) == 0o600


def test_private_control_token_is_rejected_off_loopback(control_plane_db):
    import auth
    from core.supervisor_auth import supervisor_control_headers

    auth.setup_secret(TEST_AUTH_SECRET)
    with patch("api.router_daemon._get_all_services_status", return_value=[]):
        response = asyncio.run(
            _backend_request(
                "GET",
                "/api/control/services",
                headers=supervisor_control_headers(),
                client_host="203.0.113.20",
            )
        )

    assert response.status_code == 401


def test_supervisor_commands_fail_closed_without_session(control_plane_db):
    import auth
    import supervisor

    auth.setup_secret(TEST_AUTH_SECRET)
    client = TestClient(supervisor.app)
    with patch("supervisor._start_sync") as start:
        response = client.post("/api/supervisor/backend/start")

    assert response.status_code == 401
    assert start.call_count == 0


def test_supervisor_commands_fail_closed_before_auth_setup(control_plane_db):
    import supervisor

    client = TestClient(supervisor.app)
    with patch("supervisor._start_sync") as start:
        response = client.post("/api/supervisor/backend/start")

    assert response.status_code == 428
    assert start.call_count == 0


def test_backend_control_cookie_mutation_requires_origin(control_plane_db):
    import auth
    import config

    auth.setup_secret(TEST_AUTH_SECRET)
    token, _expires_at = auth.create_session(user_agent="pytest", ip="127.0.0.1")
    headers = {"X-CSRF-Token": auth.csrf_token_for_session(token)}
    cookies = {config.SESSION_COOKIE_NAME: token}

    with patch("api.router_daemon._start_service", new=AsyncMock(return_value={"ok": True})) as start:
        rejected = asyncio.run(
            _backend_request(
                "POST",
                "/api/control/screen_watcher/start",
                headers=headers,
                client_host="127.0.0.1",
                cookies=cookies,
            )
        )
        assert rejected.status_code == 403
        assert start.call_count == 0

        accepted = asyncio.run(
            _backend_request(
                "POST",
                "/api/control/screen_watcher/start",
                headers={**headers, "Origin": "http://backend.test"},
                client_host="127.0.0.1",
                cookies=cookies,
            )
        )

    assert accepted.status_code == 200
    assert start.call_count == 1


def test_supervisor_cookie_mutation_requires_csrf_and_origin(control_plane_db):
    import auth
    import config
    import supervisor

    auth.setup_secret(TEST_AUTH_SECRET)
    token, _expires_at = auth.create_session(user_agent="pytest", ip="127.0.0.1")
    client = TestClient(supervisor.app)
    client.cookies.set(config.SESSION_COOKIE_NAME, token)
    client.headers["X-CSRF-Token"] = auth.csrf_token_for_session(token)

    with patch("supervisor._start_sync", return_value={"ok": True}) as start:
        missing_origin = client.post("/api/supervisor/backend/start")
        assert missing_origin.status_code == 403
        assert start.call_count == 0

        accepted = client.post(
            "/api/supervisor/backend/start",
            headers={"Origin": "http://testserver"},
        )

    assert accepted.status_code == 200
    assert start.call_count == 1


def _authenticated_supervisor_client() -> TestClient:
    import auth
    import config
    import supervisor

    auth.setup_secret(TEST_AUTH_SECRET)
    token, _expires_at = auth.create_session(user_agent="pytest", ip="127.0.0.1")
    client = TestClient(supervisor.app)
    client.cookies.set(config.SESSION_COOKIE_NAME, token)
    client.headers["X-CSRF-Token"] = auth.csrf_token_for_session(token)
    client.headers["Origin"] = "http://testserver"
    return client


def test_supervisor_control_failures_use_http_status_and_hide_internal_details(
    control_plane_db,
):
    client = _authenticated_supervisor_client()
    with patch(
        "supervisor._start_sync",
        return_value={"ok": False, "error": "secret-internal-path"},
    ):
        response = client.post("/api/supervisor/backend/start")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "service_start_failed",
            "message": "Impossible de démarrer le service backend",
            "context": {"service": "backend", "action": "start"},
        }
    }
    assert "secret-internal-path" not in response.text


def test_supervisor_unknown_service_is_a_structured_404(control_plane_db):
    client = _authenticated_supervisor_client()
    response = client.post("/api/supervisor/inconnu/start")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "service_not_found",
        "message": "Service inconnu : inconnu",
        "context": {"service": "inconnu", "action": "start"},
    }


def test_supervisor_upstream_failure_never_exposes_exception(control_plane_db):
    client = _authenticated_supervisor_client()
    with (
        patch("supervisor._port_open", return_value=True),
        patch(
            "supervisor._http.post",
            new=AsyncMock(side_effect=RuntimeError("token-secret")),
        ),
    ):
        response = client.post("/api/supervisor/sub/screen_watcher/start")

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "backend_control_failed",
        "message": "Le backend n'a pas pu exécuter cette action",
        "context": {"service": "screen_watcher", "action": "start"},
    }
    assert "token-secret" not in response.text


def test_supervisor_preserves_public_ollama_prerequisite(control_plane_db):
    upstream = httpx.Response(
        200,
        json={"ok": False, "error": "Ollama connection refused at secret-host"},
    )
    client = _authenticated_supervisor_client()
    with (
        patch("supervisor._port_open", return_value=True),
        patch("supervisor._http.post", new=AsyncMock(return_value=upstream)),
    ):
        response = client.post("/api/supervisor/sub/screen_watcher/start")

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "ollama_required",
        "message": "Ollama doit être démarré avant Screen Watcher",
        "context": {"service": "screen_watcher", "action": "start"},
    }
    assert "secret-host" not in response.text


def test_supervisor_proxy_unavailable_uses_shared_error_shape(control_plane_db):
    client = _authenticated_supervisor_client()
    with patch("supervisor._port_open", return_value=False):
        response = client.get("/api/tasks")

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "backend_unavailable",
        "message": "Le backend est arrêté",
    }


def test_supervisor_log_reads_are_bounded(control_plane_db, tmp_path):
    import supervisor

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "backend.log").write_text(
        "\n".join(f"line-{index}" for index in range(600)),
        encoding="utf-8",
    )
    client = _authenticated_supervisor_client()
    with patch.object(supervisor, "LOGS_DIR", logs_dir):
        response = client.get("/api/supervisor/backend/logs?lines=100000")

    assert response.status_code == 200
    assert len(response.json()["logs"]) == 500
    assert response.json()["logs"][0] == "line-100"


def test_supervisor_unknown_log_service_is_a_structured_404(control_plane_db):
    client = _authenticated_supervisor_client()
    response = client.get("/api/supervisor/inconnu/logs")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "service_not_found"


def test_supervisor_websocket_requires_session_and_valid_origin(control_plane_db):
    import auth
    import config
    import supervisor

    auth.setup_secret(TEST_AUTH_SECRET)
    client = TestClient(supervisor.app)
    with pytest.raises(WebSocketDisconnect) as unauthenticated:
        with client.websocket_connect(
            "/ws/supervisor",
            headers={"Origin": "http://testserver"},
        ):
            pass
    assert unauthenticated.value.code == 4401

    token, _expires_at = auth.create_session(user_agent="pytest", ip="127.0.0.1")
    client.cookies.set(config.SESSION_COOKIE_NAME, token)
    with pytest.raises(WebSocketDisconnect) as wrong_origin:
        with client.websocket_connect(
            "/ws/supervisor",
            headers={"Origin": "http://evil.example"},
        ):
            pass
    assert wrong_origin.value.code == 4403

    with patch("supervisor._svc_status", new=AsyncMock(return_value={"id": "ok"})):
        with client.websocket_connect(
            "/ws/supervisor",
            headers={"Origin": "http://testserver"},
        ) as websocket:
            initial = websocket.receive_json()
    assert initial["type"] == "initial_state"

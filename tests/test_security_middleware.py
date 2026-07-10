"""Tests : middleware sécurité (verrou de session, CSRF, headers), jetons device/localisation, WS."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.conftest import TEST_AUTH_SECRET, authenticate  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


# ── Verrou de session sur /api/* ──────────────────────────────

def test_protected_route_returns_428_when_not_configured(tmp_db):
    with _client() as client:
        r = client.get("/api/jarvis-journal")
    assert r.status_code == 428
    assert r.json()["error"] == "setup_required"


def test_protected_route_returns_401_when_configured_but_no_session(tmp_db):
    import auth

    auth.setup_secret(TEST_AUTH_SECRET)
    with _client() as client:
        r = client.get("/api/jarvis-journal")
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_protected_route_accessible_after_authentication(tmp_db):
    with _client() as client:
        authenticate(client)
        r = client.get("/api/jarvis-journal")
    assert r.status_code == 200


def test_auth_routes_bypass_session_gate_even_unconfigured(tmp_db):
    with _client() as client:
        r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json()["configured"] is False


def test_static_and_spa_routes_are_not_gated(tmp_db):
    with _client() as client:
        r = client.get("/manifest.json")
    # Peut 404 si le fichier n'existe pas dans ce checkout de test, mais
    # ne doit JAMAIS être bloqué par le verrou (428/401) qui ne s'applique qu'à /api/*.
    assert r.status_code != 428
    assert r.status_code != 401


# ── CSRF (Origin/Referer) ──────────────────────────────────────

def test_post_with_mismatched_origin_rejected(tmp_db):
    with _client() as client:
        authenticate(client)
        r = client.post(
            "/api/life-context",
            json={"context_type": "test", "description": "x"},
            headers={"Origin": "http://evil.example.com", "Host": "testserver"},
        )
    assert r.status_code == 403
    assert r.json()["error"] == "csrf_check_failed"


def test_post_with_matching_origin_allowed(tmp_db):
    with _client() as client:
        authenticate(client)
        r = client.post(
            "/api/life-context",
            json={"context_type": "test", "description": "x"},
            headers={"Origin": "http://testserver"},
        )
    assert r.status_code == 200


def test_post_without_origin_header_allowed(tmp_db):
    """Clients non-navigateur (scripts, TestClient) n'envoient pas Origin — protégés par SameSite."""
    with _client() as client:
        authenticate(client)
        r = client.post(
            "/api/life-context", json={"context_type": "test", "description": "x"}
        )
    assert r.status_code == 200


# ── En-têtes de sécurité ────────────────────────────────────────

def test_security_headers_present_on_every_response(tmp_db):
    with _client() as client:
        r = client.get("/api/auth/status")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("referrer-policy") == "no-referrer"
    assert "default-src 'self'" in r.headers.get("content-security-policy", "")
    assert "geolocation=(self)" in r.headers.get("permissions-policy", "")


# ── Flux /api/auth/* complet ────────────────────────────────────

def test_setup_then_unlock_then_logout_flow(tmp_db):
    with _client() as client:
        r = client.post("/api/auth/setup", json={"secret": "first-secret"})
        assert r.status_code == 200

        r2 = client.post("/api/auth/setup", json={"secret": "again"})
        assert r2.status_code == 409

        status = client.get("/api/auth/status").json()
        assert status["authenticated"] is True

        client.post("/api/auth/logout")
        status2 = client.get("/api/auth/status").json()
        assert status2["authenticated"] is False

        r3 = client.post("/api/auth/unlock", json={"secret": "first-secret"})
        assert r3.status_code == 200


def test_unlock_lockout_after_repeated_failures(tmp_db, monkeypatch):
    monkeypatch.setattr("config.AUTH_LOCKOUT_MAX_ATTEMPTS", 3)
    with _client() as client:
        client.post("/api/auth/setup", json={"secret": "correct-secret"})
        client.post("/api/auth/logout")

        for _ in range(3):
            r = client.post("/api/auth/unlock", json={"secret": "wrong"})
            assert r.status_code == 401

        r = client.post("/api/auth/unlock", json={"secret": "correct-secret"})
        assert r.status_code == 429


def test_change_secret_revokes_other_sessions(tmp_db):
    with _client() as client:
        authenticate(client)
        r = client.post(
            "/api/auth/change-secret",
            json={"current": TEST_AUTH_SECRET, "new": "brand-new-secret"},
        )
        assert r.status_code == 200
        # L'ancien secret ne fonctionne plus
        r2 = client.post("/api/auth/unlock", json={"secret": TEST_AUTH_SECRET})
        assert r2.status_code == 401
        r3 = client.post("/api/auth/unlock", json={"secret": "brand-new-secret"})
        assert r3.status_code == 200


def test_sessions_list_and_revoke(tmp_db):
    with _client() as client:
        authenticate(client)
        sessions = client.get("/api/auth/sessions").json()["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["current"] is True

        session_id = sessions[0]["id"]
        r = client.post(f"/api/auth/sessions/{session_id}/revoke")
        assert r.status_code == 200

        # La session courante vient d'être révoquée → route protégée refuse maintenant
        r2 = client.get("/api/jarvis-journal")
        assert r2.status_code == 401


def test_revoke_unknown_session_404(tmp_db):
    with _client() as client:
        authenticate(client)
        r = client.post("/api/auth/sessions/999999/revoke")
    assert r.status_code == 404


# ── Jeton device (heartbeat / screen) ───────────────────────────

def test_device_register_then_heartbeat_requires_token(tmp_db):
    with _client() as client:
        reg = client.post(
            "/api/devices/register",
            json={"device_id": "mac-test", "device_name": "Mac Test"},
        )
        assert reg.status_code == 200
        token = reg.json()["token"]

        no_token = client.post("/api/devices/mac-test/heartbeat")
        assert no_token.status_code == 401

        wrong_token = client.post(
            "/api/devices/mac-test/heartbeat", headers={"X-Device-Token": "wrong"}
        )
        assert wrong_token.status_code == 401

        ok = client.post(
            "/api/devices/mac-test/heartbeat", headers={"X-Device-Token": token}
        )
        assert ok.status_code == 200


def test_heartbeat_unknown_device_404(tmp_db):
    with _client() as client:
        r = client.post(
            "/api/devices/never-registered/heartbeat", headers={"X-Device-Token": "x"}
        )
    assert r.status_code == 404


def test_activate_device_requires_session_not_device_token(tmp_db):
    """`/activate` est déclenché depuis le dashboard navigateur — verrou de session, pas jeton device."""
    import auth

    auth.setup_secret(TEST_AUTH_SECRET)
    with _client() as client:
        client.post(
            "/api/devices/register", json={"device_id": "mac-test", "device_name": "Mac"}
        )
        no_auth = client.post("/api/devices/mac-test/activate")
        assert no_auth.status_code == 401

        authenticate(client)
        ok = client.post("/api/devices/mac-test/activate")
        assert ok.status_code == 200


# ── Jeton localisation partagé (Shortcuts iOS) ─────────────────

def test_location_post_open_when_token_unset(tmp_db, monkeypatch):
    monkeypatch.setattr("config.LOCATION_API_TOKEN", "")
    with _client() as client:
        r = client.post("/api/location", json={"latitude": 50.6, "longitude": 3.0})
    assert r.status_code != 401


def test_location_post_requires_token_when_configured(tmp_db, monkeypatch):
    monkeypatch.setattr("config.LOCATION_API_TOKEN", "shared-secret-token")
    with _client() as client:
        no_token = client.post("/api/location", json={"latitude": 50.6, "longitude": 3.0})
        assert no_token.status_code == 401

        ok = client.post(
            "/api/location",
            json={"latitude": 50.6, "longitude": 3.0},
            headers={"X-Location-Token": "shared-secret-token"},
        )
        assert ok.status_code != 401


# ── WebSocket ────────────────────────────────────────────────────

def test_ws_rejected_when_not_configured(tmp_db):
    import main
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    with TestClient(main.app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws"):
                pass
        assert exc_info.value.code == 4428


def test_ws_rejected_without_session(tmp_db):
    import auth
    import main
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    auth.setup_secret(TEST_AUTH_SECRET)
    with TestClient(main.app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws"):
                pass
        assert exc_info.value.code == 4401

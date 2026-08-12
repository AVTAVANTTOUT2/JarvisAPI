"""Tests : middleware sécurité (verrou de session, CSRF, headers), jetons device/localisation, WS."""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.conftest import TEST_AUTH_SECRET, authenticate  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.AUTH_PROGRESSIVE_DELAY_SECONDS", 0)
    monkeypatch.setattr("config.AUTH_GLOBAL_MAX_ATTEMPTS", 50)
    monkeypatch.setattr("config.CSRF_ALLOWED_ORIGINS", "")
    from database import init_db

    init_db()
    return db_path


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def _pair_remote_device(client, device_id: str = "mac-test") -> str:
    authenticate(client)
    start = client.post("/api/devices/pairing/start")
    assert start.status_code == 200
    code = start.json()["code"]
    client.cookies.clear()
    response = client.post(
        "/api/devices/register",
        json={
            "device_id": device_id,
            "device_name": "Mac Test",
            "pairing_code": code,
        },
    )
    assert response.status_code == 200
    return response.json()["token"]


def _screen_png_b64(width: int = 16, height: int = 12) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(20, 40, 60)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


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
    assert r.json()["csrf_token"] is None


def test_local_recovery_route_rejects_remote_clients(tmp_db):
    import auth

    auth.setup_secret(TEST_AUTH_SECRET)
    with _client() as client:
        r = client.post(
            "/api/auth/local-unlock",
            json={"secret": TEST_AUTH_SECRET},
            headers={"X-Jarvis-Local-Recovery": "1"},
        )
    assert r.status_code == 403


def test_local_recovery_rejects_loopback_proxy_with_remote_host():
    from starlette.requests import Request

    from api.router_auth import _is_loopback

    proxied = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/local-unlock",
            "headers": [(b"host", b"jarvis.example")],
            "client": ("127.0.0.1", 54321),
        }
    )
    local = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/local-unlock",
            "headers": [(b"host", b"localhost:8000")],
            "client": ("127.0.0.1", 54321),
        }
    )

    assert _is_loopback(proxied) is False
    assert _is_loopback(local) is True


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "POST",
            "/api/auth/change-secret",
            {"current": TEST_AUTH_SECRET, "new": "attacker-secret"},
        ),
        ("GET", "/api/auth/sessions", None),
        ("POST", "/api/auth/sessions/1/revoke", None),
        ("POST", "/api/auth/logout", None),
    ],
)
def test_sensitive_auth_routes_require_session(tmp_db, method, path, body):
    import auth

    auth.setup_secret(TEST_AUTH_SECRET)
    with _client() as client:
        r = client.request(method, path, json=body)
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


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


def test_post_with_origin_containing_host_as_substring_rejected(tmp_db):
    """« testserver.evil.com » contient « testserver » — un test en sous-chaîne le laisserait passer."""
    with _client() as client:
        authenticate(client)
        r = client.post(
            "/api/life-context",
            json={"context_type": "test", "description": "x"},
            headers={"Origin": "http://testserver.evil.com"},
        )
    assert r.status_code == 403


def test_post_with_same_hostname_different_port_rejected(tmp_db):
    with _client() as client:
        authenticate(client)
        r = client.post(
            "/api/life-context",
            json={"context_type": "test", "description": "x"},
            headers={"Origin": "http://testserver:5173"},
        )
    assert r.status_code == 403
    assert r.json()["error"] == "csrf_check_failed"


def test_explicit_dev_proxy_origin_allowed(tmp_db, monkeypatch):
    monkeypatch.setattr(
        "config.CSRF_ALLOWED_ORIGINS",
        "https://localhost:5173, https://testserver:5173",
    )
    with _client() as client:
        authenticate(client)
        r = client.post(
            "/api/life-context",
            json={"context_type": "test", "description": "x"},
            headers={"Origin": "https://testserver:5173"},
        )
    assert r.status_code == 200


def test_post_with_matching_origin_allowed(tmp_db):
    with _client() as client:
        authenticate(client)
        r = client.post(
            "/api/life-context",
            json={"context_type": "test", "description": "x"},
            headers={"Origin": "http://testserver"},
        )
    assert r.status_code == 200


def test_cors_is_empty_by_default_and_uses_only_explicit_exact_origins():
    from api.middleware import configured_cors_origins

    assert configured_cors_origins("") == []
    assert configured_cors_origins(
        "https://localhost:5173, http://0.0.0.0:3000/path, "
        "https://localhost:5173, https://jarvis.example:8443"
    ) == ["https://localhost:5173", "https://jarvis.example:8443"]


def test_supervisor_preserved_host_matches_exact_origin(tmp_db):
    with _client() as client:
        authenticate(client)
        r = client.post(
            "/api/life-context",
            json={"context_type": "test", "description": "x"},
            headers={
                "Host": "localhost:9000",
                "Origin": "http://localhost:9000",
            },
        )
    assert r.status_code == 200


def test_reverse_proxy_uses_public_https_scheme_for_csrf(monkeypatch):
    from starlette.requests import Request

    from api.middleware import _csrf_origin_allowed

    monkeypatch.setattr("config.WEB_HTTPS_BEHIND_PROXY", True)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/life-context",
            "headers": [
                (b"host", b"jarvis.example.ts.net"),
                (b"origin", b"https://jarvis.example.ts.net"),
            ],
            "client": ("127.0.0.1", 54321),
            "server": ("127.0.0.1", 8080),
        }
    )

    assert _csrf_origin_allowed(request) is True


def test_cookie_mutation_without_origin_header_rejected(tmp_db):
    """Une mutation navigateur portée par cookie exige une Origin exacte."""
    with _client() as client:
        authenticate(client)
        del client.headers["Origin"]
        r = client.post(
            "/api/life-context", json={"context_type": "test", "description": "x"}
        )
    assert r.status_code == 403
    assert r.json()["error"] == "csrf_check_failed"


def test_post_without_csrf_token_rejected_even_same_origin(tmp_db):
    with _client() as client:
        authenticate(client)
        del client.headers["X-CSRF-Token"]
        r = client.post(
            "/api/life-context",
            json={"context_type": "test", "description": "x"},
            headers={"Origin": "http://testserver"},
        )
    assert r.status_code == 403
    assert r.json()["error"] == "csrf_check_failed"


def test_post_with_invalid_csrf_token_rejected(tmp_db):
    with _client() as client:
        authenticate(client)
        r = client.post(
            "/api/life-context",
            json={"context_type": "test", "description": "x"},
            headers={
                "Origin": "http://testserver",
                "X-CSRF-Token": "invalid-token",
            },
        )
    assert r.status_code == 403


def test_logout_requires_csrf_token_and_preserves_session_on_rejection(tmp_db):
    with _client() as client:
        authenticate(client)
        del client.headers["X-CSRF-Token"]
        rejected = client.post(
            "/api/auth/logout",
            headers={"Origin": "http://testserver"},
        )
        assert rejected.status_code == 403
        assert client.get("/api/auth/status").json()["authenticated"] is True


# ── En-têtes de sécurité ────────────────────────────────────────


def _assert_security_headers(response):
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "no-referrer"
    csp = response.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp
    assert "connect-src 'self' ws:" not in csp
    assert "connect-src 'self' wss:" not in csp
    assert "geolocation=(self)" in response.headers.get("permissions-policy", "")


def test_security_headers_present_on_public_response(tmp_db):
    with _client() as client:
        r = client.get("/api/auth/status")
    assert r.status_code == 200
    _assert_security_headers(r)


def test_security_headers_present_on_setup_required_response(tmp_db):
    with _client() as client:
        r = client.get("/api/jarvis-journal")
    assert r.status_code == 428
    _assert_security_headers(r)


def test_security_headers_present_on_unauthorized_response(tmp_db):
    import auth

    auth.setup_secret(TEST_AUTH_SECRET)
    with _client() as client:
        r = client.get("/api/jarvis-journal")
    assert r.status_code == 401
    _assert_security_headers(r)


def test_security_headers_present_on_csrf_rejection(tmp_db):
    with _client() as client:
        authenticate(client)
        del client.headers["X-CSRF-Token"]
        r = client.post(
            "/api/life-context",
            json={"context_type": "test", "description": "x"},
            headers={"Origin": "http://testserver"},
        )
    assert r.status_code == 403
    _assert_security_headers(r)


def test_hsts_present_on_early_response_when_https_enabled(tmp_db, monkeypatch):
    import auth

    monkeypatch.setattr("config.WEB_HTTPS", True)
    auth.setup_secret(TEST_AUTH_SECRET)
    with _client() as client:
        r = client.get("/api/jarvis-journal")
    assert r.status_code == 401
    assert r.headers.get("strict-transport-security") == (
        "max-age=31536000; includeSubDomains"
    )


def test_handler_cannot_weaken_non_csp_security_headers():
    """Un handler qui pose X-Frame-Options/Referrer-Policy avant le middleware
    ne doit pas pouvoir assouplir la politique globale — seule la CSP est
    remplaable (page HTML liée par hashes).
    """
    from starlette.responses import Response

    from api.middleware import _apply_security_headers
    from security_headers import SECURITY_HEADERS

    response = Response("ok", media_type="text/plain")
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "unsafe-url"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'"

    _apply_security_headers(response)

    assert response.headers["X-Frame-Options"] == SECURITY_HEADERS["X-Frame-Options"]
    assert response.headers["Referrer-Policy"] == SECURITY_HEADERS["Referrer-Policy"]
    assert response.headers["X-Content-Type-Options"] == (
        SECURITY_HEADERS["X-Content-Type-Options"]
    )
    # CSP fournie par la route : conservée (plus stricte / liée au contenu).
    assert response.headers["Content-Security-Policy"] == "default-src 'none'"


def test_route_overridable_security_headers_is_csp_only():
    from api.middleware import _ROUTE_OVERRIDABLE_SECURITY_HEADERS

    assert _ROUTE_OVERRIDABLE_SECURITY_HEADERS == frozenset(
        {"Content-Security-Policy"}
    )


def test_proxy_https_sets_hsts_and_secure_session_cookie(tmp_db, monkeypatch):
    monkeypatch.setattr("config.WEB_HTTPS", False)
    monkeypatch.setattr("config.WEB_HTTPS_BEHIND_PROXY", True)
    with _client() as client:
        r = client.post("/api/auth/setup", json={"secret": TEST_AUTH_SECRET})
    assert r.status_code == 200
    assert r.headers.get("strict-transport-security") == (
        "max-age=31536000; includeSubDomains"
    )
    assert "secure" in r.headers["set-cookie"].lower()


# ── Flux /api/auth/* complet ────────────────────────────────────

def test_setup_then_unlock_then_logout_flow(tmp_db):
    with _client() as client:
        r = client.post("/api/auth/setup", json={"secret": "first-secret"})
        assert r.status_code == 200
        client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        client.headers["Origin"] = "http://testserver"

        r2 = client.post("/api/auth/setup", json={"secret": "again"})
        assert r2.status_code == 409

        status = client.get("/api/auth/status").json()
        assert status["authenticated"] is True
        assert status["csrf_token"] == r.json()["csrf_token"]
        assert status["csrf_token"] != client.cookies.get("jarvis_session")
        assert client.get("/api/auth/status").headers["Cache-Control"] == "no-store"

        logout = client.post("/api/auth/logout")
        assert logout.status_code == 200
        status2 = client.get("/api/auth/status").json()
        assert status2["authenticated"] is False

        r3 = client.post("/api/auth/unlock", json={"secret": "first-secret"})
        assert r3.status_code == 200


def test_unlock_lockout_after_repeated_failures(tmp_db, monkeypatch):
    monkeypatch.setattr("config.AUTH_LOCKOUT_MAX_ATTEMPTS", 3)
    with _client() as client:
        setup = client.post("/api/auth/setup", json={"secret": "correct-secret"})
        client.headers["X-CSRF-Token"] = setup.json()["csrf_token"]
        client.headers["Origin"] = "http://testserver"
        assert client.post("/api/auth/logout").status_code == 200

        for _ in range(3):
            r = client.post("/api/auth/unlock", json={"secret": "wrong"})
            assert r.status_code == 401

        r = client.post("/api/auth/unlock", json={"secret": "correct-secret"})
        assert r.status_code == 429
        assert int(r.headers["Retry-After"]) > 0


def test_unlock_enforces_progressive_delay(tmp_db, monkeypatch):
    monkeypatch.setattr("config.AUTH_LOCKOUT_MAX_ATTEMPTS", 5)
    monkeypatch.setattr("config.AUTH_PROGRESSIVE_DELAY_SECONDS", 2)
    with _client() as client:
        setup = client.post("/api/auth/setup", json={"secret": "correct-secret"})
        client.headers["X-CSRF-Token"] = setup.json()["csrf_token"]
        client.headers["Origin"] = "http://testserver"
        assert client.post("/api/auth/logout").status_code == 200

        first = client.post("/api/auth/unlock", json={"secret": "wrong"})
        assert first.status_code == 401

        immediate_retry = client.post(
            "/api/auth/unlock",
            json={"secret": "correct-secret"},
        )
        assert immediate_retry.status_code == 429
        assert int(immediate_retry.headers["Retry-After"]) > 0


def test_loopback_recovery_clears_global_lock_and_opens_session(tmp_db, monkeypatch):
    import auth
    import api.router_auth as router_auth

    monkeypatch.setattr("config.AUTH_LOCKOUT_MAX_ATTEMPTS", 100)
    monkeypatch.setattr("config.AUTH_GLOBAL_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(router_auth, "_is_loopback", lambda _request: True)
    auth.setup_secret(TEST_AUTH_SECRET)
    auth.record_failed_attempt(
        auth.client_rate_key("203.0.113.1", channel="web"),
        channel="web",
    )
    auth.record_failed_attempt(
        auth.client_rate_key("203.0.113.2", channel="web"),
        channel="web",
    )
    assert auth.rate_limit_status(
        auth.client_rate_key("127.0.0.1", channel="web")
    ).scope == "global"

    with _client() as client:
        status = client.get("/api/auth/status").json()
        assert status["local_recovery_available"] is True
        assert status["locked_out"] is True

        recovered = client.post(
            "/api/auth/local-unlock",
            json={"secret": TEST_AUTH_SECRET},
            headers={"X-Jarvis-Local-Recovery": "1"},
        )
        assert recovered.status_code == 200
        assert recovered.json()["recovered"] is True
        assert client.get("/api/jarvis-journal").status_code == 200

    assert auth.rate_limit_status(
        auth.client_rate_key("127.0.0.1", channel="web")
    ).blocked is False


def test_change_secret_uses_unlock_lockout(tmp_db, monkeypatch):
    monkeypatch.setattr("config.AUTH_LOCKOUT_MAX_ATTEMPTS", 3)
    with _client() as client:
        authenticate(client)

        for _ in range(3):
            r = client.post(
                "/api/auth/change-secret",
                json={"current": "wrong-secret", "new": "attacker-secret"},
            )
            assert r.status_code == 401

        r = client.post(
            "/api/auth/change-secret",
            json={"current": TEST_AUTH_SECRET, "new": "brand-new-secret"},
        )
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
        token = _pair_remote_device(client)

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


def test_remote_screen_uses_public_analysis_api(tmp_db, monkeypatch):
    from scripts.screen_watcher import screen_watcher

    analyze = AsyncMock(
        return_value={
            "app": "Safari",
            "activity": "lecture documentation",
            "mood": "focused",
            "notable": None,
        }
    )
    monkeypatch.setattr(screen_watcher, "analyze_image", analyze)
    with _client() as client:
        token = _pair_remote_device(client, "screen-public-api")
        response = client.post(
            "/api/devices/screen-public-api/screen",
            headers={"X-Device-Token": token},
            json={"image_b64": _screen_png_b64(), "app": "Safari", "change_pct": 12.5},
        )

    assert response.status_code == 200, response.text
    assert response.json()["analysis"]["activity"] == "lecture documentation"
    analyze.assert_awaited_once()
    assert analyze.await_args.kwargs["app"] == "Safari"
    assert analyze.await_args.kwargs["window_info"] == {"width": 16, "height": 12}


def test_remote_screen_rejects_excessive_pixel_count_before_analysis(tmp_db, monkeypatch):
    import config
    from scripts.screen_watcher import screen_watcher

    monkeypatch.setattr(config, "REMOTE_SCREEN_MAX_PIXELS", 50)
    analyze = AsyncMock()
    monkeypatch.setattr(screen_watcher, "analyze_image", analyze)
    with _client() as client:
        token = _pair_remote_device(client, "screen-pixels")
        response = client.post(
            "/api/devices/screen-pixels/screen",
            headers={"X-Device-Token": token},
            json={"image_b64": _screen_png_b64(10, 10), "app": "Safari"},
        )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "image_dimensions_exceeded"
    analyze.assert_not_awaited()


def test_remote_screen_content_length_is_rejected_before_json_parse(tmp_db, monkeypatch):
    import config

    monkeypatch.setattr(config, "REMOTE_SCREEN_MAX_REQUEST_BYTES", 100)
    with _client() as client:
        token = _pair_remote_device(client, "screen-body-limit")
        response = client.post(
            "/api/devices/screen-body-limit/screen",
            headers={"X-Device-Token": token},
            json={"image_b64": "A" * 500, "app": "Safari"},
        )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "payload_too_large"


def test_agentic_body_is_rejected_before_json_parse_without_capping_uploads(
    tmp_db,
    monkeypatch,
):
    import config

    monkeypatch.setattr(config, "AGENTIC_MAX_REQUEST_BYTES", 128)
    with _client() as client:
        authenticate(client)
        response = client.post(
            "/api/agentic/runs",
            headers={"Idempotency-Key": "oversized-agentic-payload"},
            json={"title": "A" * 500},
        )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "payload_too_large"
    from api.middleware import _request_size_limit

    assert _request_size_limit("POST", "/upload") is None


def test_agentic_stream_without_content_length_is_rejected_before_parsing(tmp_db):
    with _client() as client:
        authenticate(client)
        response = client.post(
            "/api/agentic/runs",
            headers={
                "Idempotency-Key": "chunked-agentic-payload",
                "Content-Type": "application/json",
                "Transfer-Encoding": "chunked",
            },
            content=iter([b'{"title":"test"}']),
        )

    assert response.status_code == 411
    assert response.json()["detail"]["code"] == "length_required"


def test_activate_device_requires_session_not_device_token(tmp_db):
    """`/activate` est déclenché depuis le dashboard navigateur — verrou de session, pas jeton device."""
    import auth

    auth.setup_secret(TEST_AUTH_SECRET)
    with _client() as client:
        token = _pair_remote_device(client)
        no_auth = client.post("/api/devices/mac-test/activate")
        assert no_auth.status_code == 401

        # Le cœur du contrat : un jeton device valide reste insuffisant ici.
        # Sinon une machine appairée pourrait se promouvoir active toute seule.
        with_device_token = client.post(
            "/api/devices/mac-test/activate", headers={"X-Device-Token": token}
        )
        assert with_device_token.status_code == 401

        authenticate(client)
        ok = client.post("/api/devices/mac-test/activate")
        assert ok.status_code == 200


# ── Jeton localisation partagé (Shortcuts iOS) ─────────────────

def test_location_post_closed_when_token_unset(tmp_db, monkeypatch):
    monkeypatch.setattr("config.LOCATION_API_TOKEN", "")
    with _client() as client:
        r = client.post("/api/location", json={"latitude": 50.6, "longitude": 3.0})
    assert r.status_code == 503
    assert "non configurée" in r.json()["detail"]


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
        assert ok.status_code == 200


def test_location_query_token_is_rejected_to_avoid_secret_leaks(tmp_db, monkeypatch):
    monkeypatch.setattr("config.LOCATION_API_TOKEN", "shared-secret-token")
    with _client() as client:
        r = client.post(
            "/api/location?token=shared-secret-token",
            json={"latitude": 50.6, "longitude": 3.0},
        )
    assert r.status_code == 401


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (91, 3),
        (50, 181),
        ("nan", 3),
        (50, "inf"),
    ],
)
def test_location_post_rejects_invalid_coordinates(
    tmp_db,
    monkeypatch,
    latitude,
    longitude,
):
    monkeypatch.setattr("config.LOCATION_API_TOKEN", "shared-secret-token")
    with _client() as client:
        r = client.post(
            "/api/location",
            json={"latitude": latitude, "longitude": longitude},
            headers={"X-Location-Token": "shared-secret-token"},
        )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert any(item["loc"][-1] in {"latitude", "longitude"} for item in detail)


def test_location_ingestion_is_rate_limited_before_auth(tmp_db, monkeypatch):
    from api import router_location

    monkeypatch.setattr("config.LOCATION_API_TOKEN", "shared-secret-token")
    monkeypatch.setattr("config.LOCATION_RATE_LIMIT_REQUESTS", 2)
    monkeypatch.setattr("config.LOCATION_RATE_LIMIT_WINDOW_SECONDS", 60)
    with router_location._location_rate_limit_lock:
        router_location._location_rate_limit_buckets.clear()

    try:
        with _client() as client:
            for _ in range(2):
                denied = client.post(
                    "/api/location",
                    json={"latitude": 50.6, "longitude": 3.0},
                )
                assert denied.status_code == 401
            limited = client.post(
                "/api/location",
                json={"latitude": 50.6, "longitude": 3.0},
            )
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) >= 1
    finally:
        with router_location._location_rate_limit_lock:
            router_location._location_rate_limit_buckets.clear()


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


def test_remote_screen_without_content_length_is_refused(tmp_db):
    """Un corps chunké contournait le plafond et se bufferisait entièrement.

    Le garde-fou ne lisait `Content-Length` que s'il était présent : une requête
    en `Transfer-Encoding: chunked` traversait la borne déclarée et FastAPI
    parsait un corps de taille arbitraire avant toute validation.
    """
    with _client() as client:
        token = _pair_remote_device(client, "screen-chunked")
        response = client.post(
            "/api/devices/screen-chunked/screen",
            headers={
                "X-Device-Token": token,
                "Content-Type": "application/json",
                "Transfer-Encoding": "chunked",
            },
            content=iter([b'{"image_b64":"AAAA","app":"Safari"}']),
        )

    assert response.status_code == 411
    assert response.json()["detail"]["code"] == "length_required"


def test_mobile_voice_turn_without_content_length_is_refused(tmp_db):
    """Même frontière que l'écran distant : le tour vocal mobile a un plafond.

    PR #182 a verrouillé le cas chunké pour `/api/devices/{id}/screen` ; la
    même fonction `_content_length_error` borne aussi `/api/mobile/voice/turn`.
    Sans ce test, une régression pourrait ne casser que la route Companion.
    """
    with _client() as client:
        authenticate(client)
        start = client.post("/api/mobile/pairing/start")
        assert start.status_code == 200
        code = start.json()["code"]
        complete = client.post(
            "/api/mobile/pairing/complete",
            json={
                "code": code,
                "device_id": "voice-chunked",
                "name": "Pixel Chunked",
                "model": "Pixel 8",
                "app_version": "1.0.4",
            },
        )
        assert complete.status_code == 200
        token = complete.json()["token"]
        response = client.post(
            "/api/mobile/voice/turn",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "multipart/form-data; boundary=----jarvis",
                "Transfer-Encoding": "chunked",
            },
            content=iter([b"------jarvis--\r\n"]),
        )

    assert response.status_code == 411
    assert response.json()["detail"]["code"] == "length_required"


@pytest.mark.parametrize(
    "path",
    [
        "/api/devices/screen-bad-cl/screen",
        "/api/mobile/voice/turn",
        "/api/agentic/runs",
    ],
)
@pytest.mark.parametrize("raw_length", ["abc", "-1"])
def test_capped_routes_reject_invalid_content_length(path: str, raw_length: str):
    """Un Content-Length illisible ou négatif ne doit pas passer le plafond.

    Testé via `_content_length_error` directement : TestClient/httpx recalcule
    souvent `Content-Length` à partir du corps, ce qui masquerait le garde-fou.
    """
    from types import SimpleNamespace

    from api.middleware import _content_length_error

    request = SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path=path),
        headers={"content-length": raw_length},
    )
    response = _content_length_error(request)
    assert response is not None
    assert response.status_code == 400
    assert response.body
    import json

    assert json.loads(response.body)["detail"]["code"] == "invalid_content_length"


def test_routes_without_declared_limit_still_accept_streamed_bodies():
    """L'exigence ne vaut que pour les routes à plafond déclaré.

    Le chemin témoin est volontairement inexistant : citer une vraie route
    inscrirait un lien de couverture mensonger dans `architecture_truth.json`,
    qui associe chaque route aux tests qui la nomment.
    """
    from api.middleware import _request_size_limit

    assert _request_size_limit("POST", "/api/sans-plafond-declare") is None
    assert _request_size_limit("POST", "/api/mobile/voice/turn") is not None
    assert _request_size_limit("GET", "/api/mobile/voice/turn") is None
    assert _request_size_limit("POST", "/api/devices/x/screen") is not None
    assert _request_size_limit("POST", "/api/agentic/runs") is not None
    assert _request_size_limit("POST", "/upload") is None
    assert _request_size_limit("GET", "/api/devices/x/screen") is None


# ── Contrat : aucune route applicative hors du verrou ────────────


#: Routes délibérément atteignables sans cookie de session. Chacune
#: s'authentifie autrement (secret d'ouverture, code d'appairage, jeton device
#: ou jeton de localisation) ou sert précisément à ouvrir une session.
PUBLIC_BY_DESIGN: frozenset[tuple[str, str]] = frozenset({
    # Sonde de vie : publique par nécessité, et volontairement muette —
    # `tests/test_health_contract.py` verrouille le fait qu'elle ne renvoie
    # rien d'autre que `{"status": "ok"}`.
    ("GET", "/api/health/live"),
    ("GET", "/api/auth/status"),
    ("POST", "/api/auth/setup"),
    ("POST", "/api/auth/unlock"),
    ("POST", "/api/auth/local-unlock"),
    ("POST", "/api/auth/verify"),
    ("POST", "/api/location"),
    ("POST", "/api/location/batch"),
    ("POST", "/api/devices/register"),
    ("POST", "/api/mobile/pairing/complete"),
    ("POST", "/api/mobile/session"),
    ("POST", "/api/mobile/push-token"),
    ("POST", "/api/mobile/capabilities"),
    ("POST", "/api/mobile/voice/turn"),
    ("POST", "/api/mobile/conversations"),
    ("POST", "/api/mobile/chat"),
    ("POST", "/api/mobile/chat/confirm"),
})


def _declared_operations() -> list[tuple[str, str]]:
    import main

    operations: list[tuple[str, str]] = []
    for path, methods in main.app.openapi().get("paths", {}).items():
        if "{" in path:
            continue
        for method in methods:
            verb = method.upper()
            if verb in ("GET", "POST", "PATCH", "DELETE", "PUT"):
                operations.append((verb, path))
    return sorted(set(operations))


def test_no_route_escapes_the_session_gate(tmp_db):
    """Balaie tout le contrat public sans session et exige un refus.

    Le verrou ne regardait que le préfixe `/api/`. `POST /upload`, déclaré
    parmi ses voisins `/api/*` sans leur préfixe, acceptait donc un fichier de
    n'importe qui atteignant le port — application verrouillée ou non, avec
    écriture disque, insertion en base et injection du contenu dans le contexte
    LLM. Ce test énumère les routes plutôt que d'en surveiller une.
    """
    import auth

    auth.setup_secret(TEST_AUTH_SECRET)
    reachable: list[tuple[int, str, str]] = []
    with _client() as client:
        for method, path in _declared_operations():
            if (method, path) in PUBLIC_BY_DESIGN:
                continue
            response = client.request(method, path, json={})
            if response.status_code not in (401, 403, 428, 405):
                reachable.append((response.status_code, method, path))

    assert reachable == [], (
        "routes atteignables sans session : "
        + ", ".join(f"{s} {m} {p}" for s, m, p in reachable)
    )


def test_upload_requires_a_session(tmp_db):
    """Preuve directe : l'upload anonyme écrivait un fichier et une ligne."""
    import auth

    auth.setup_secret(TEST_AUTH_SECRET)
    with _client() as client:
        response = client.post(
            "/upload",
            files={"file": ("poc.txt", b"contenu injecte", "text/plain")},
        )

    assert response.status_code == 401

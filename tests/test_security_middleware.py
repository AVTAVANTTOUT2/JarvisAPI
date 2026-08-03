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


def test_root_spa_binds_next_inline_bootstrap_to_exact_csp_hashes(tmp_db):
    """Régression page noire : les scripts RSC doivent être autorisés par hash exact."""
    with _client() as client:
        r = client.get("/")
    if r.status_code != 200:
        pytest.skip("frontend/out absent dans ce checkout")
    html = r.text
    # La charge RSC est poussée par un script inline : elle doit être présente
    # ET non vide. `self.__next_f` seul passerait sur une coquille sans données.
    assert "self.__next_f" in html
    assert "self.__next_f.push([1," in html
    # Le chunk d'entrée doit être référencé, sinon rien n'hydrate le shell.
    # (Le marqueur `jarvis-loading` a été retiré avec le layout mobile client :
    # la détection est côté serveur, cet état de chargement n'existe plus.)
    assert "/_next/static/chunks/" in html
    from security_headers import inline_csp_hashes

    script_hashes, _style_hashes = inline_csp_hashes(html)
    csp = r.headers.get("content-security-policy", "")
    assert script_hashes
    assert all(source in csp for source in script_hashes)
    assert "script-src 'self' 'unsafe-inline'" not in csp


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
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_coordinates"


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

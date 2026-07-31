"""Contrat du pairage natif Android et révocation à distance."""

from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.conftest import authenticate  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "mobile.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def _client():
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)


def _pair(client, device_id: str = "s24-test") -> str:
    start = client.post("/api/mobile/pairing/start")
    assert start.status_code == 200
    code = start.json()["code"]
    complete = client.post(
        "/api/mobile/pairing/complete",
        json={
            "code": code,
            "device_id": device_id,
            "name": "Galaxy S24",
            "model": "SM-S921B",
            "app_version": "1.0.0",
        },
    )
    assert complete.status_code == 200
    return complete.json()["token"]


def test_pairing_start_requires_authenticated_session(tmp_db):
    """Régression : cookie Secure (WEB_HTTPS) ne doit pas casser le TestClient."""
    import config

    assert config.WEB_HTTPS is False
    with _client() as client:
        authenticate(client)
        start = client.post("/api/mobile/pairing/start")
    assert start.status_code == 200
    assert len(start.json()["code"]) == 6


def test_pairing_code_is_one_time_and_token_is_hashed(tmp_db):
    import auth
    from database import get_db

    with _client() as client:
        authenticate(client)
        start = client.post("/api/mobile/pairing/start")
        code = start.json()["code"]
        payload = {"code": code, "device_id": "s24", "name": "Galaxy S24"}
        first = client.post("/api/mobile/pairing/complete", json=payload)
        second = client.post("/api/mobile/pairing/complete", json=payload)

    assert first.status_code == 200
    assert second.status_code == 401
    raw_token = first.json()["token"]
    with get_db() as conn:
        stored = conn.execute(
            "SELECT token_hash FROM mobile_devices WHERE device_id = 's24'"
        ).fetchone()[0]
    assert stored == auth.hash_token(raw_token)
    assert raw_token != stored


def test_mobile_pairing_locks_after_five_failures_with_retry_after(tmp_db):
    with _client() as client:
        authenticate(client)
        valid_code = client.post("/api/mobile/pairing/start").json()["code"]
        payload = {"code": "000000", "device_id": "locked-phone"}

        for _ in range(4):
            assert client.post("/api/mobile/pairing/complete", json=payload).status_code == 401
        blocked = client.post("/api/mobile/pairing/complete", json=payload)
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) >= 14 * 60

        payload["code"] = valid_code
        still_blocked = client.post("/api/mobile/pairing/complete", json=payload)
        assert still_blocked.status_code == 429
        assert int(still_blocked.headers["Retry-After"]) > 0


def test_mobile_pairing_succeeds_after_lockout_expires(tmp_db, monkeypatch):
    from database import get_db

    monkeypatch.setattr("config.DEVICE_PAIRING_MAX_ATTEMPTS", 2)
    with _client() as client:
        authenticate(client)
        valid_code = client.post("/api/mobile/pairing/start").json()["code"]
        payload = {"code": "000000", "device_id": "retry-phone"}
        assert client.post("/api/mobile/pairing/complete", json=payload).status_code == 401
        assert client.post("/api/mobile/pairing/complete", json=payload).status_code == 429

        with get_db() as conn:
            conn.execute(
                """UPDATE device_pairing_attempts SET blocked_until = ?
                   WHERE client_key LIKE 'mobile:%'""",
                (
                    (
                        datetime.now(timezone.utc).replace(tzinfo=None)
                        - timedelta(seconds=1)
                    ).isoformat(timespec="seconds"),
                ),
            )

        payload["code"] = valid_code
        accepted = client.post("/api/mobile/pairing/complete", json=payload)
        assert accepted.status_code == 200


def test_mobile_pairing_code_has_exactly_one_concurrent_consumer(tmp_db):
    import auth
    import config
    from database import consume_mobile_pairing_code

    with _client() as client:
        authenticate(client)
        code = client.post("/api/mobile/pairing/start").json()["code"]

    workers = 8
    barrier = threading.Barrier(workers)

    def consume(index: int) -> tuple[str, int]:
        barrier.wait()
        return consume_mobile_pairing_code(
            auth.hash_token(f"pair:{code}"),
            f"concurrent-{index}",
            max_attempts=config.DEVICE_PAIRING_MAX_ATTEMPTS,
            window_minutes=config.DEVICE_PAIRING_ATTEMPT_WINDOW_MINUTES,
            lockout_minutes=config.DEVICE_PAIRING_LOCKOUT_MINUTES,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(consume, range(workers)))

    assert [status for status, _ in results].count("ok") == 1
    assert [status for status, _ in results].count("invalid") == workers - 1


def test_mobile_lockout_does_not_lock_desktop_pairing(tmp_db, monkeypatch):
    monkeypatch.setattr("config.DEVICE_PAIRING_MAX_ATTEMPTS", 2)
    with _client() as client:
        authenticate(client)
        mobile_payload = {"code": "000000", "device_id": "blocked-mobile"}
        assert client.post("/api/mobile/pairing/complete", json=mobile_payload).status_code == 401
        assert client.post("/api/mobile/pairing/complete", json=mobile_payload).status_code == 429

        desktop_code = client.post("/api/devices/pairing/start").json()["code"]
        client.cookies.clear()
        desktop = client.post(
            "/api/devices/register",
            json={
                "device_id": "desktop-after-mobile-lockout",
                "device_name": "Desktop",
                "pairing_code": desktop_code,
            },
        )

    assert desktop.status_code == 200


def test_native_token_opens_cookie_session_and_registers_push(tmp_db):
    from database import get_db

    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()

        session = client.post(
            "/api/mobile/session", headers={"Authorization": f"Bearer {token}"}
        )
        assert session.status_code == 200
        assert client.get("/api/auth/status").json()["authenticated"] is True

        push = client.post(
            "/api/mobile/push-token",
            headers={"Authorization": f"Bearer {token}"},
            json={"token": "fcm-token-test"},
        )
        assert push.status_code == 200

    with get_db() as conn:
        row = conn.execute(
            "SELECT fcm_token FROM mobile_devices WHERE device_id = 's24-test'"
        ).fetchone()
    assert row[0] == "fcm-token-test"


def test_revoking_phone_revokes_native_and_cookie_sessions(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()
        assert client.post(
            "/api/mobile/session", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 200

        # Une autre session web privée administre les téléphones.
        client.cookies.clear()
        authenticate(client)
        assert client.post("/api/mobile/devices/s24-test/revoke").status_code == 200

        client.cookies.clear()
        denied = client.post(
            "/api/mobile/session", headers={"Authorization": f"Bearer {token}"}
        )
        assert denied.status_code == 401


def test_location_accepts_native_bearer(tmp_db, monkeypatch):
    monkeypatch.setattr("config.LOCATION_API_TOKEN", "legacy-secret")
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()
        response = client.post(
            "/api/location",
            headers={"Authorization": f"Bearer {token}"},
            json={"latitude": 50.6292, "longitude": 3.0573, "source": "android"},
        )
    assert response.status_code == 200

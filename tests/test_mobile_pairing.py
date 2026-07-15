"""Contrat du pairage natif Android et révocation à distance."""

from __future__ import annotations

import sys
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
    import main
    from fastapi.testclient import TestClient

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

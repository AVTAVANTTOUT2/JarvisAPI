"""Pairage et cycle de vie sécurisé des agents desktop distants."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.conftest import TEST_AUTH_SECRET, authenticate  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "devices.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def _pairing_code(client) -> str:
    authenticate(client)
    response = client.post("/api/devices/pairing/start")
    assert response.status_code == 200
    code = response.json()["code"]
    assert len(code) == 6 and code.isdigit()
    return code


def _register(client, device_id: str = "macbook-test") -> tuple[str, str]:
    code = _pairing_code(client)
    client.cookies.clear()
    response = client.post(
        "/api/devices/register",
        json={
            "device_id": device_id,
            "device_name": "MacBook Test",
            "device_type": "laptop",
            "pairing_code": code,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["token"], code


def test_pairing_start_requires_browser_session(tmp_db):
    import auth

    auth.setup_secret(TEST_AUTH_SECRET)
    with _client() as client:
        response = client.post("/api/devices/pairing/start")
    assert response.status_code == 401


def test_registration_requires_one_time_code_and_hashes_token(tmp_db):
    import auth
    from database import get_db

    with _client() as client:
        token, code = _register(client)

        replay = client.post(
            "/api/devices/register",
            json={
                "device_id": "another-mac",
                "device_name": "Another Mac",
                "pairing_code": code,
            },
        )
        assert replay.status_code == 401

        authenticate(client)
        listed = client.get("/api/devices")
        assert listed.status_code == 200
        serialized = repr(listed.json())
        assert token not in serialized
        assert "token_hash" not in serialized
        assert "auth_token" not in serialized

    with get_db() as conn:
        row = conn.execute(
            "SELECT token_hash FROM devices WHERE device_id = 'macbook-test'"
        ).fetchone()
    assert row["token_hash"] == auth.hash_token(token)
    assert row["token_hash"] != token


def test_existing_device_never_returns_token(tmp_db):
    with _client() as client:
        first_token, _ = _register(client)
        code = _pairing_code(client)
        client.cookies.clear()
        duplicate = client.post(
            "/api/devices/register",
            json={
                "device_id": "macbook-test",
                "device_name": "Impostor",
                "pairing_code": code,
            },
        )

    assert duplicate.status_code == 409
    assert "token" not in duplicate.json()
    assert first_token not in duplicate.text


def test_registration_attempts_are_rate_limited_by_client(tmp_db, monkeypatch):
    monkeypatch.setattr("config.DEVICE_PAIRING_MAX_ATTEMPTS", 3)
    with _client() as client:
        valid_code = _pairing_code(client)
        client.cookies.clear()
        payload = {
            "device_id": "rate-limited-mac",
            "device_name": "Rate Limited",
            "pairing_code": "000000",
        }
        assert client.post("/api/devices/register", json=payload).status_code == 401
        assert client.post("/api/devices/register", json=payload).status_code == 401
        blocked = client.post("/api/devices/register", json=payload)
        assert blocked.status_code == 429
        assert int(blocked.headers["retry-after"]) > 0

        payload["pairing_code"] = valid_code
        still_blocked = client.post("/api/devices/register", json=payload)
        assert still_blocked.status_code == 429


def test_device_token_contract_covers_heartbeat_tts_rotation_and_revoke(tmp_db):
    with _client() as client:
        old_token, _ = _register(client)

        assert client.post("/api/devices/macbook-test/heartbeat").status_code == 401
        assert client.post(
            "/api/devices/macbook-test/heartbeat",
            headers={"Authorization": f"Bearer {old_token}"},
        ).status_code == 401
        assert client.post(
            "/api/devices/macbook-test/heartbeat",
            headers={"X-Device-Token": old_token},
        ).status_code == 200

        assert client.get("/api/devices/macbook-test/tts").status_code == 401
        assert client.get(
            "/api/devices/macbook-test/tts",
            headers={"X-Device-Token": old_token},
        ).status_code == 200

        authenticate(client)
        rotated = client.post("/api/devices/macbook-test/token/rotate")
        assert rotated.status_code == 200
        new_token = rotated.json()["token"]
        assert new_token != old_token

        client.cookies.clear()
        assert client.post(
            "/api/devices/macbook-test/heartbeat",
            headers={"X-Device-Token": old_token},
        ).status_code == 401
        assert client.post(
            "/api/devices/macbook-test/heartbeat",
            headers={"X-Device-Token": new_token},
        ).status_code == 200

        authenticate(client)
        assert client.post("/api/devices/macbook-test/revoke").status_code == 200
        client.cookies.clear()
        assert client.post(
            "/api/devices/macbook-test/heartbeat",
            headers={"X-Device-Token": new_token},
        ).status_code == 401


def test_legacy_plaintext_token_is_migrated_and_cleared(monkeypatch, tmp_path):
    import auth
    import database

    db_path = tmp_path / "legacy-devices.db"
    raw_token = "legacy-device-token"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT UNIQUE NOT NULL,
                device_name TEXT NOT NULL,
                auth_token TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            "INSERT INTO devices (device_id, device_name, auth_token) VALUES (?, ?, ?)",
            ("legacy-mac", "Legacy Mac", raw_token),
        )

    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()

    with database.get_db() as conn:
        row = conn.execute(
            "SELECT auth_token, token_hash FROM devices WHERE device_id = 'legacy-mac'"
        ).fetchone()
    assert row["auth_token"] is None
    assert row["token_hash"] == auth.hash_token(raw_token)

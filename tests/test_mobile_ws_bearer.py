"""WebSocket : cookie session ou Bearer mobile au handshake."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.conftest import authenticate  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "ws_mobile.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def _pair(client) -> str:
    start = client.post("/api/mobile/pairing/start")
    code = start.json()["code"]
    complete = client.post(
        "/api/mobile/pairing/complete",
        json={
            "code": code,
            "device_id": "ws-phone",
            "name": "S24",
            "model": "test",
            "app_version": "2.0.0-alpha02",
        },
    )
    return complete.json()["token"]


def test_websocket_accepts_mobile_bearer(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()
        with client.websocket_connect(
            "/ws",
            headers={"Authorization": f"Bearer {token}"},
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "connected"
            assert "conversation_id" in msg


def test_websocket_rejects_without_auth(tmp_db):
    with _client() as client:
        authenticate(client)  # configure PIN
        client.cookies.clear()
        with pytest.raises(Exception):
            with client.websocket_connect("/ws"):
                pass

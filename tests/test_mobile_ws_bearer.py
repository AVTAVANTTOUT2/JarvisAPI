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
    from api.ws_session import _ws_last_sessions

    init_db()
    _ws_last_sessions.clear()
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
            assert "checkpoint_id" in msg


def test_websocket_checkpoint_resumes_after_reconnect(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()
        headers = {"Authorization": f"Bearer {token}"}
        with client.websocket_connect("/ws", headers=headers) as ws:
            first = ws.receive_json()

        checkpoint_id = first["checkpoint_id"]
        with client.websocket_connect(
            f"/ws?checkpoint_id={checkpoint_id}",
            headers=headers,
        ) as ws:
            resumed = ws.receive_json()

    assert resumed["conversation_id"] == first["conversation_id"]
    assert resumed["checkpoint_id"] == checkpoint_id
    assert resumed["resumed"] is True


def test_websocket_rejects_invalid_checkpoint(tmp_db):
    from starlette.websockets import WebSocketDisconnect

    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                "/ws?checkpoint_id=not-a-checkpoint",
                headers={"Authorization": f"Bearer {token}"},
            ):
                pass
    assert exc_info.value.code == 4400


def test_websocket_cookie_requires_exact_origin(tmp_db):
    from starlette.websockets import WebSocketDisconnect

    with _client() as client:
        authenticate(client)
        client.headers.pop("Origin", None)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws"):
                pass
        assert exc_info.value.code == 4401

        client.headers["Origin"] = "http://testserver"
        for headers in (
            {"Origin": "http://evil.example"},
            {"Origin": "http://testserver:8080"},
        ):
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws", headers=headers):
                    pass
            assert exc_info.value.code == 4401

        with client.websocket_connect(
            "/ws",
            headers={"Origin": "http://testserver"},
        ) as ws:
            assert ws.receive_json()["type"] == "connected"


def test_mobile_bearer_ignores_browser_origin_policy(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()
        with client.websocket_connect(
            "/ws",
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "https://native-app.invalid",
            },
        ) as ws:
            assert ws.receive_json()["type"] == "connected"


def test_websocket_rejects_without_auth(tmp_db):
    with _client() as client:
        authenticate(client)  # configure PIN
        client.cookies.clear()
        with pytest.raises(Exception):
            with client.websocket_connect("/ws"):
                pass


def test_mobile_confirmation_requires_boolean_and_refusal_consumes_proposal(tmp_db):
    from api.action_confirmations import store_pending_proposal

    with _client() as client:
        authenticate(client)
        token = _pair(client)
        headers = {"Authorization": f"Bearer {token}"}
        conversation = client.post(
            "/api/mobile/conversations", headers=headers, json={},
        ).json()["conversation_id"]
        pending = store_pending_proposal(
            {"type": "task", "title": "ne jamais créer"},
            conversation_id=conversation,
            session_id="mobile:ws-phone",
        )
        string_false = client.post(
            "/api/mobile/chat/confirm",
            headers=headers,
            json={
                "conversation_id": conversation,
                "proposal_id": pending["proposal_id"],
                "confirmed": "false",
            },
        )
        assert string_false.status_code == 422

        refused = client.post(
            "/api/mobile/chat/confirm",
            headers=headers,
            json={
                "conversation_id": conversation,
                "proposal_id": pending["proposal_id"],
                "confirmed": False,
            },
        )
        assert refused.status_code == 200
        assert refused.json()["cancelled"] is True

        replay = client.post(
            "/api/mobile/chat/confirm",
            headers=headers,
            json={
                "conversation_id": conversation,
                "proposal_id": pending["proposal_id"],
                "confirmed": True,
            },
        )
        assert replay.status_code == 409

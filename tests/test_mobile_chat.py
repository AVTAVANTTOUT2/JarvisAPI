"""Contrats chat mobile Android — REST Bearer + idempotence + mutations conversations."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.conftest import authenticate  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "mobile_chat.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def _pair(client, device_id: str = "chat-phone") -> str:
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
            "app_version": "2.0.0-alpha02",
        },
    )
    assert complete.status_code == 200
    return complete.json()["token"]


def test_mobile_create_conversation_with_bearer(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()
        response = client.post(
            "/api/mobile/conversations",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Test Android"},
        )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["conversation_id"], int)
    assert str(uuid.UUID(body["checkpoint_id"])) == body["checkpoint_id"]
    assert body["title"] == "Test Android"
    assert body["title_status"] == "manual"


def test_mobile_checkpoint_creation_and_resume_are_idempotent(tmp_db):
    checkpoint_id = str(uuid.uuid4())
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()
        headers = {"Authorization": f"Bearer {token}"}
        first = client.post(
            "/api/mobile/conversations",
            headers=headers,
            json={"checkpoint_id": checkpoint_id},
        )
        second = client.post(
            "/api/mobile/conversations",
            headers=headers,
            json={"checkpoint_id": checkpoint_id},
        )

    assert first.status_code == second.status_code == 200
    assert first.json()["conversation_id"] == second.json()["conversation_id"]
    assert first.json()["resumed"] is False
    assert second.json()["resumed"] is True


def test_mobile_chat_and_idempotent_replay(tmp_db):
    fake_result = {
        "text": "Bonjour Monsieur.",
        "emotion": "warm",
        "action": None,
        "action_result": None,
        "agent": "info",
        "model": "test",
        "cost": 0.0,
    }
    with patch(
        "api.router_mobile_chat._process_message_internal",
        new_callable=AsyncMock,
        return_value=fake_result,
    ) as mocked:
        with _client() as client:
            authenticate(client)
            token = _pair(client)
            client.cookies.clear()
            headers = {"Authorization": f"Bearer {token}"}
            create = client.post(
                "/api/mobile/conversations",
                headers=headers,
                json={},
            )
            conv_id = create.json()["conversation_id"]
            checkpoint_id = create.json()["checkpoint_id"]
            payload = {
                "content": "Salut JARVIS",
                "conversation_id": conv_id,
                "checkpoint_id": checkpoint_id,
                "client_message_id": "req-abc-12345",
            }
            first = client.post("/api/mobile/chat", headers=headers, json=payload)
            second = client.post("/api/mobile/chat", headers=headers, json=payload)

    assert first.status_code == 200
    assert first.json()["response_text"] == "Bonjour Monsieur."
    assert first.json()["checkpoint_id"] == checkpoint_id
    assert first.json()["idempotent_replay"] is False
    assert second.status_code == 200
    assert second.json()["response_text"] == "Bonjour Monsieur."
    assert second.json()["idempotent_replay"] is True
    assert mocked.await_count == 1


def test_mobile_chat_rejects_mismatched_checkpoint(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()
        headers = {"Authorization": f"Bearer {token}"}
        first = client.post("/api/mobile/conversations", headers=headers, json={})
        second = client.post("/api/mobile/conversations", headers=headers, json={})
        response = client.post(
            "/api/mobile/chat",
            headers=headers,
            json={
                "content": "Ne mélange pas ces conversations",
                "conversation_id": first.json()["conversation_id"],
                "checkpoint_id": second.json()["checkpoint_id"],
            },
        )

    assert response.status_code == 409


def test_mobile_chat_unknown_conversation_404(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()
        response = client.post(
            "/api/mobile/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": "hi", "conversation_id": 999999},
        )
    assert response.status_code == 404


def test_mobile_chat_bodies_are_strict_and_forbid_unknown_fields(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()
        headers = {"Authorization": f"Bearer {token}"}

        coerced_title = client.post(
            "/api/mobile/conversations",
            headers=headers,
            json={"title": 123},
        )
        coerced_content = client.post(
            "/api/mobile/chat",
            headers=headers,
            json={"content": 123},
        )
        unknown = client.post(
            "/api/mobile/chat",
            headers=headers,
            json={"content": "Bonjour", "debug": True},
        )

    assert coerced_title.status_code == 422
    assert coerced_content.status_code == 422
    assert unknown.status_code == 422


def test_mobile_chat_auth_precedes_body_validation(tmp_db):
    with _client() as client:
        authenticate(client)
        client.cookies.clear()
        response = client.post("/api/mobile/chat", json={"content": 123})

    assert response.status_code == 401


def test_bearer_can_pin_and_rename_conversation(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()
        headers = {"Authorization": f"Bearer {token}"}
        create = client.post("/api/mobile/conversations", headers=headers, json={})
        conv_id = create.json()["conversation_id"]

        rename = client.patch(
            f"/api/conversations/{conv_id}",
            headers=headers,
            json={"title": "Nouveau titre"},
        )
        pin = client.post(f"/api/conversations/{conv_id}/pin", headers=headers)
        renamed_detail = client.get(
            f"/api/conversations/{conv_id}",
            headers=headers,
        )
        clear_title = client.patch(
            f"/api/conversations/{conv_id}",
            headers=headers,
            json={"title": ""},
        )
        cleared_detail = client.get(
            f"/api/conversations/{conv_id}",
            headers=headers,
        )

    assert rename.status_code == 200
    assert pin.status_code == 200
    assert renamed_detail.status_code == 200
    assert renamed_detail.json()["title"] == "Nouveau titre"
    assert renamed_detail.json()["title_status"] == "manual"
    assert renamed_detail.json()["title_source"] == "user"
    assert clear_title.status_code == 200
    assert cleared_detail.json()["title"] is None
    assert cleared_detail.json()["title_status"] == "pending"


def test_tasks_post_still_requires_session(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()
        response = client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "nope"},
        )
    assert response.status_code == 401


def test_revoked_token_cannot_chat(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client, device_id="revoke-chat")
        client.cookies.clear()
        authenticate(client)
        assert client.post("/api/mobile/devices/revoke-chat/revoke").status_code == 200
        client.cookies.clear()
        response = client.post(
            "/api/mobile/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": "hello"},
        )
    assert response.status_code == 401

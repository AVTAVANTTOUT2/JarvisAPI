"""Contrats 404 des mutations CRUD sur une ressource absente."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import authenticate


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "crud-not-found.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)

    from database import init_db

    init_db()
    return db_path


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def test_missing_crud_resources_return_404(tmp_db):
    mutations = [
        ("patch", "/api/conversations/999999", {"title": "absente"}),
        ("delete", "/api/conversations/999999", None),
        ("post", "/api/conversations/999999/archive", None),
        ("put", "/api/places/999999", {"name": "absent"}),
        ("post", "/api/devices/never-registered/activate", None),
        ("patch", "/api/tasks/999999", {"status": "done"}),
        ("delete", "/api/tasks/999999", None),
        ("post", "/api/notifications/999999/read", None),
        ("put", "/api/life-profile/999999", {"content": "absente"}),
        ("delete", "/api/life-profile/999999", None),
        ("patch", "/api/commitments/999999", {"status": "kept"}),
        ("post", "/api/auth/sessions/999999/revoke", None),
        ("post", "/api/mobile/devices/never-paired/revoke", None),
    ]

    with _client() as client:
        authenticate(client)
        for method, path, payload in mutations:
            response = client.request(method, path, json=payload)
            assert response.status_code == 404, f"{method} {path}: {response.text}"


def test_unknown_device_activation_keeps_current_device_active(tmp_db):
    from database import get_active_device, register_local_device, set_active_device

    register_local_device("mac-mini", "Mac mini", "desktop")
    assert set_active_device("mac-mini") is True

    with _client() as client:
        authenticate(client)
        response = client.post("/api/devices/never-registered/activate")

    assert response.status_code == 404
    assert get_active_device()["device_id"] == "mac-mini"


def test_existing_conversation_place_and_device_mutations_still_succeed(tmp_db):
    from database import (
        create_conversation,
        get_active_device,
        get_conversation_detail,
        register_local_device,
    )
    from database.location_helpers import create_place, get_place

    conversation_id = create_conversation()
    place_id = create_place("Bureau", "work", 48.85, 2.35)
    register_local_device("mac-mini", "Mac mini", "desktop")

    with _client() as client:
        authenticate(client)
        rename = client.patch(
            f"/api/conversations/{conversation_id}",
            json={"title": "Conversation renommée"},
        )
        archive = client.post(f"/api/conversations/{conversation_id}/archive")
        update_place = client.put(
            f"/api/places/{place_id}",
            json={"name": "Bureau principal"},
        )
        activate = client.post("/api/devices/mac-mini/activate")
        delete = client.delete(f"/api/conversations/{conversation_id}")

    assert rename.status_code == 200
    assert archive.status_code == 200
    assert update_place.status_code == 200
    assert activate.status_code == 200
    assert delete.status_code == 200
    assert get_conversation_detail(conversation_id) is None
    assert get_place(place_id)["name"] == "Bureau principal"
    assert get_active_device()["device_id"] == "mac-mini"


def test_place_update_rejects_payload_without_mutable_fields(tmp_db):
    with _client() as client:
        authenticate(client)
        response = client.put("/api/places/999999", json={"unknown": "value"})

    assert response.status_code == 400

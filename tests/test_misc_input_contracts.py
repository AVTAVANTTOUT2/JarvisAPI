"""Contrats stricts des mutations API hors domaines spécialisés."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import authenticate


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "misc-input-contracts.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)

    from database import init_db

    init_db()
    return db_path


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def test_devagent_autorun_rejects_coercion_empty_and_unknown_fields(tmp_db: Path) -> None:
    with _client() as client:
        authenticate(client)
        responses = (
            client.post("/api/devagent/autorun", json={"description": 123}),
            client.post("/api/devagent/autorun", json={"description": "   "}),
            client.post(
                "/api/devagent/autorun",
                json={"description": "Construis un outil", "unattended": True},
            ),
        )

    assert [response.status_code for response in responses] == [422, 422, 422]


def test_conversation_patch_is_strict_and_requires_a_known_field(tmp_db: Path) -> None:
    from database import create_conversation, get_conversation_detail

    conversation_id = create_conversation(agent="tests")
    with _client() as client:
        authenticate(client)
        valid = client.patch(
            f"/api/conversations/{conversation_id}",
            json={"title": "Titre", "pinned": True},
        )
        coerced = client.patch(
            f"/api/conversations/{conversation_id}",
            json={"pinned": "true"},
        )
        unknown = client.patch(
            f"/api/conversations/{conversation_id}",
            json={"agent": "admin"},
        )
        empty = client.patch(f"/api/conversations/{conversation_id}", json={})

    assert valid.status_code == 200
    assert get_conversation_detail(conversation_id)["title"] == "Titre"
    assert [coerced.status_code, unknown.status_code, empty.status_code] == [422, 422, 422]


def test_document_privacy_update_rejects_coercion_and_extra_fields(tmp_db: Path) -> None:
    with _client() as client:
        authenticate(client)
        coerced = client.put(
            "/api/privacy/documents",
            json={"strict_local": "false"},
        )
        unknown = client.put(
            "/api/privacy/documents",
            json={"strict_local": False, "cloud": True},
        )

    assert coerced.status_code == 422
    assert unknown.status_code == 422


def test_dnd_accepts_default_and_rejects_invalid_minutes(tmp_db: Path) -> None:
    with _client() as client:
        authenticate(client)
        default = client.post("/api/dnd")
        coerced = client.post("/api/dnd", json={"minutes": "60"})
        too_large = client.post("/api/dnd", json={"minutes": 1_441})
        unknown = client.post("/api/dnd", json={"minutes": 60, "silent": True})

    assert default.status_code == 200
    assert [coerced.status_code, too_large.status_code, unknown.status_code] == [422, 422, 422]

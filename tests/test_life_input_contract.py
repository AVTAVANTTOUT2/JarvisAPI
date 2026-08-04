"""Validation stricte des mutations Life profile/context et Journal."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def life_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "life-input-contract.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)

    from database import init_db
    from main import app
    from tests.conftest import authenticate

    init_db()
    with TestClient(app) as client:
        authenticate(client)
        yield client


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"category": "unknown", "content": "Texte"},
        {"category": "goals", "content": True},
        {"category": "goals", "content": "Texte", "extra": 1},
    ],
)
def test_life_profile_rejects_invalid_payloads_with_422(life_client, payload):
    response = life_client.post("/api/life-profile", json=payload)
    assert response.status_code == 422, response.text


def test_life_profile_update_rejects_unknown_fields(life_client):
    response = life_client.put(
        "/api/life-profile/1",
        json={"content": "Texte", "category": "goals"},
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"context_type": "travail", "description": ""},
        {"context_type": True, "description": "Changement"},
        {"context_type": "travail", "description": "Changement", "extra": 1},
    ],
)
def test_life_context_rejects_invalid_payloads_with_422(life_client, payload):
    response = life_client.post("/api/life-context", json=payload)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("payload", [{}, {"content": True}, {"content": "x", "extra": 1}])
def test_journal_rejects_invalid_payload_before_pipeline(life_client, payload):
    response = life_client.post("/api/journal", json=payload)
    assert response.status_code == 422, response.text


def test_valid_life_payloads_are_trimmed_and_persisted(life_client):
    profile = life_client.post(
        "/api/life-profile",
        json={"category": "goals", "content": "  Finir le projet  "},
    )
    context = life_client.post(
        "/api/life-context",
        json={"context_type": "  travail  ", "description": "  Nouvelle équipe  "},
    )
    assert profile.status_code == 200, profile.text
    assert profile.json()["content"] == "Finir le projet"
    assert context.status_code == 200, context.text
    assert context.json()["context_type"] == "travail"

"""Validation 422 des mutations Tasks avant tout accès à la base."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def task_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "task-input-contract.db"
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
        {"title": ""},
        {"title": True},
        {"title": "Valide", "priority": "urgent"},
        {"title": "Valide", "champ_inconnu": "refusé"},
    ],
)
def test_task_creation_rejects_invalid_payloads_with_422(task_client, payload):
    response = task_client.post("/api/tasks", json=payload)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"status": "DONE"},
        {"status": True},
        {"status": "done", "champ_inconnu": "refusé"},
    ],
)
def test_task_update_rejects_invalid_payloads_with_422(task_client, payload):
    response = task_client.patch("/api/tasks/1", json=payload)
    assert response.status_code == 422, response.text


def test_valid_task_payload_is_normalized_and_persisted(task_client):
    created = task_client.post(
        "/api/tasks",
        json={"title": "  Préparer la revue  ", "priority": "high"},
    )
    assert created.status_code == 200, created.text
    task = created.json()["task"]
    assert task["title"] == "Préparer la revue"
    assert task["priority"] == "high"

    updated = task_client.patch(f"/api/tasks/{task['id']}", json={"status": "done"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["task"]["status"] == "done"

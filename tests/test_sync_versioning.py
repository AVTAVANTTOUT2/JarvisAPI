from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import database
from api.sync_versioning import operation_checksum, sync_versioning_middleware
from database.core import init_db


@pytest.fixture()
def sync_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "jarvis.db"))
    init_db()
    app = FastAPI()
    app.middleware("http")(sync_versioning_middleware)
    state = {"writes": 0}

    @app.get("/api/tasks")
    async def get_tasks():
        return {"writes": state["writes"]}

    @app.post("/api/tasks")
    async def create_task(request: Request):
        payload = await request.json()
        state["writes"] += 1
        return {"writes": state["writes"], "title": payload["title"]}

    with TestClient(app) as client:
        yield client, state


def _sync_headers(body: bytes, *, version: int | None = 0, operation_id: str | None = None):
    headers = {
        "Content-Type": "application/json",
        "X-Jarvis-Sync-Operation": "1",
        "X-Idempotency-Key": operation_id or str(uuid.uuid4()),
        "X-Jarvis-Operation-Checksum": operation_checksum("POST", "/api/tasks", body),
    }
    if version is not None:
        headers["X-Jarvis-Entity-Version"] = str(version)
    return headers


def test_get_exposes_durable_entity_version_and_online_mutation_bumps_it(sync_client) -> None:
    client, state = sync_client

    assert client.get("/api/tasks").headers["X-Jarvis-Entity-Version"] == "0"
    response = client.post("/api/tasks", json={"title": "online"})

    assert response.status_code == 200
    assert response.headers["X-Jarvis-Entity-Version"] == "1"
    assert client.get("/api/tasks").headers["X-Jarvis-Entity-Version"] == "1"
    assert state["writes"] == 1


def test_stale_offline_mutation_becomes_an_explicit_conflict(sync_client) -> None:
    client, state = sync_client
    client.post("/api/tasks", json={"title": "other device"})
    body = b'{"title":"offline"}'

    response = client.post("/api/tasks", content=body, headers=_sync_headers(body, version=0))

    assert response.status_code == 409
    assert response.json() == {
        "error": "sync_version_conflict",
        "entity_key": "/api/tasks",
        "client_version": 0,
        "server_version": 1,
    }
    assert state["writes"] == 1


def test_client_wins_is_an_explicit_resolution_and_advances_version(sync_client) -> None:
    client, state = sync_client
    client.post("/api/tasks", json={"title": "other device"})
    body = b'{"title":"offline"}'
    headers = _sync_headers(body, version=0)
    headers["X-Jarvis-Conflict-Strategy"] = "client_wins"

    response = client.post("/api/tasks", content=body, headers=headers)

    assert response.status_code == 200
    assert response.headers["X-Jarvis-Entity-Version"] == "2"
    assert state["writes"] == 2


def test_completed_operation_is_replayed_without_duplicate_side_effect(sync_client) -> None:
    client, state = sync_client
    body = b'{"title":"offline"}'
    operation_id = str(uuid.uuid4())
    headers = _sync_headers(body, version=0, operation_id=operation_id)

    first = client.post("/api/tasks", content=body, headers=headers)
    replay = client.post("/api/tasks", content=body, headers=headers)

    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.headers["X-Jarvis-Idempotent-Replay"] == "true"
    assert state["writes"] == 1


def test_checksum_and_base_version_are_mandatory(sync_client) -> None:
    client, state = sync_client
    body = b'{"title":"offline"}'
    bad_checksum = _sync_headers(body)
    bad_checksum["X-Jarvis-Operation-Checksum"] = "0" * 64

    assert client.post("/api/tasks", content=body, headers=bad_checksum).status_code == 400
    missing_version = _sync_headers(body, version=None)
    response = client.post("/api/tasks", content=body, headers=missing_version)
    assert response.status_code == 409
    assert response.json()["error"] == "sync_base_version_required"
    assert state["writes"] == 0

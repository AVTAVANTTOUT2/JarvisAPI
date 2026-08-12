from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import database
from api.sync_versioning import operation_checksum, sync_versioning_middleware
from database.core import get_db, init_db


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


def test_client_wins_cannot_reexecute_incomplete_operation(sync_client) -> None:
    """client_wins ne doit pas réappliquer une mutation déjà commitée côté serveur."""
    client, state = sync_client
    body = b'{"title":"offline"}'
    operation_id = str(uuid.uuid4())
    checksum = operation_checksum("POST", "/api/tasks", body)
    headers = _sync_headers(body, version=0, operation_id=operation_id)
    headers["X-Jarvis-Conflict-Strategy"] = "client_wins"

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO sync_operations (
                operation_id, checksum, entity_key, base_version,
                resolved_version, status_code
            ) VALUES (?, ?, '/api/tasks', 0, 0, 0)
            """,
            (operation_id, checksum),
        )

    state["writes"] = 1
    response = client.post("/api/tasks", content=body, headers=headers)

    assert response.status_code == 409
    assert response.json()["error"] == "sync_operation_outcome_unknown"
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


def _seed_inflight_reservation(
    *,
    operation_id: str,
    checksum: str,
    entity_key: str = "/api/tasks",
    base_version: int = 0,
) -> None:
    """Simule un crash mid-mutation : réservation présente, status_code = 0."""
    from database.core import get_db

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO sync_operations (
                operation_id, checksum, entity_key, base_version,
                resolved_version, status_code
            ) VALUES (?, ?, ?, ?, ?, 0)
            """,
            (operation_id, checksum, entity_key, base_version, base_version),
        )


def test_inflight_operation_blocks_replay_until_client_wins(sync_client) -> None:
    client, state = sync_client
    body = b'{"title":"offline"}'
    operation_id = str(uuid.uuid4())
    checksum = operation_checksum("POST", "/api/tasks", body)
    _seed_inflight_reservation(operation_id=operation_id, checksum=checksum)
    headers = _sync_headers(body, version=0, operation_id=operation_id)

    blocked = client.post("/api/tasks", content=body, headers=headers)
    assert blocked.status_code == 409
    assert blocked.json() == {
        "error": "sync_operation_outcome_unknown",
        "entity_key": "/api/tasks",
        "server_version": 0,
    }
    assert state["writes"] == 0

    headers["X-Jarvis-Conflict-Strategy"] = "client_wins"
    recovered = client.post("/api/tasks", content=body, headers=headers)
    assert recovered.status_code == 200
    assert recovered.json()["title"] == "offline"
    assert recovered.headers["X-Jarvis-Entity-Version"] == "1"
    assert state["writes"] == 1


def test_same_operation_id_with_different_checksum_is_rejected(sync_client) -> None:
    client, state = sync_client
    body = b'{"title":"offline"}'
    operation_id = str(uuid.uuid4())
    first_headers = _sync_headers(body, version=0, operation_id=operation_id)
    assert client.post("/api/tasks", content=body, headers=first_headers).status_code == 200

    other_body = b'{"title":"tampered"}'
    reused = _sync_headers(other_body, version=1, operation_id=operation_id)
    response = client.post("/api/tasks", content=other_body, headers=reused)

    assert response.status_code == 409
    assert response.json() == {
        "error": "sync_operation_reused",
        "entity_key": "/api/tasks",
    }
    assert state["writes"] == 1


def test_invalid_operation_id_and_entity_version_are_rejected(sync_client) -> None:
    client, state = sync_client
    body = b'{"title":"offline"}'

    bad_id = _sync_headers(body, version=0, operation_id="not-a-uuid")
    assert client.post("/api/tasks", content=body, headers=bad_id).json()["error"] == (
        "sync_operation_id_invalid"
    )

    bad_version = _sync_headers(body, version=0)
    bad_version["X-Jarvis-Entity-Version"] = "latest"
    response = client.post("/api/tasks", content=body, headers=bad_version)
    assert response.status_code == 400
    assert response.json() == {
        "error": "sync_entity_version_invalid",
        "entity_key": "/api/tasks",
    }
    assert state["writes"] == 0


def test_failed_handler_discards_reservation_so_retry_is_not_stuck(sync_client) -> None:
    """Un 5xx ne doit pas laisser une réservation status_code=0 bloquante."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from api.sync_versioning import sync_versioning_middleware
    from database.core import get_db

    _ = sync_client  # DB déjà initialisée par la fixture
    app = FastAPI()
    app.middleware("http")(sync_versioning_middleware)
    calls = {"n": 0}

    @app.post("/api/tasks")
    async def flaky_create(request: Request):
        calls["n"] += 1
        payload = await request.json()
        if calls["n"] == 1:
            return JSONResponse({"error": "boom"}, status_code=503)
        return {"ok": True, "title": payload["title"]}

    body = b'{"title":"retry-me"}'
    operation_id = str(uuid.uuid4())
    headers = _sync_headers(body, version=0, operation_id=operation_id)

    with TestClient(app) as client:
        first = client.post("/api/tasks", content=body, headers=headers)
        assert first.status_code == 503
        with get_db() as conn:
            row = conn.execute(
                "SELECT status_code FROM sync_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        assert row is None

        second = client.post("/api/tasks", content=body, headers=headers)
        assert second.status_code == 200
        assert second.json()["title"] == "retry-me"
        assert second.headers["X-Jarvis-Entity-Version"] == "1"


def test_sync_headers_on_non_allowlisted_mutation_bypass_protocol(sync_client) -> None:
    """Food/auth et autres effets externes ne doivent pas entrer dans le protocole."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.sync_versioning import (
        is_sync_mutation_allowed,
        sync_entity_key,
        sync_versioning_middleware,
    )

    assert is_sync_mutation_allowed("/api/food/cart/prepare", "POST") is False
    assert is_sync_mutation_allowed("/api/auth/unlock", "POST") is False
    assert sync_entity_key("/api/food/cart/prepare") is None
    assert sync_entity_key("/api/auth/unlock") is None

    _ = sync_client
    app = FastAPI()
    app.middleware("http")(sync_versioning_middleware)
    hits = {"n": 0}

    @app.post("/api/food/cart/prepare")
    async def prepare_cart():
        hits["n"] += 1
        return {"planned": True}

    body = b'{"restaurant":"x"}'
    # Même envoi des en-têtes sync : le middleware doit laisser passer sans idempotence.
    headers = {
        "Content-Type": "application/json",
        "X-Jarvis-Sync-Operation": "1",
        "X-Idempotency-Key": str(uuid.uuid4()),
        "X-Jarvis-Operation-Checksum": operation_checksum(
            "POST", "/api/food/cart/prepare", body
        ),
        "X-Jarvis-Entity-Version": "0",
    }
    with TestClient(app) as client:
        first = client.post("/api/food/cart/prepare", content=body, headers=headers)
        second = client.post("/api/food/cart/prepare", content=body, headers=headers)
    assert first.status_code == second.status_code == 200
    assert hits["n"] == 2
    assert "X-Jarvis-Idempotent-Replay" not in second.headers

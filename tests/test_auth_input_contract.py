"""Contrats 422 Auth sans affaiblir les gardes exécutées avant le corps."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def auth_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "auth-input-contract.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)

    from database import init_db
    from main import app

    init_db()
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("payload", [{}, {"secret": True}, {"secret": "ok", "extra": 1}])
def test_setup_rejects_invalid_payloads_with_422(auth_client, payload):
    response = auth_client.post("/api/auth/setup", json=payload)
    assert response.status_code == 422, response.text


def test_second_setup_guard_runs_before_body_validation(auth_client):
    assert auth_client.post("/api/auth/setup", json={"secret": "1234"}).status_code == 200
    response = auth_client.post("/api/auth/setup", json={})
    assert response.status_code == 409, response.text


def test_unlock_configuration_guard_runs_before_body_validation(auth_client):
    response = auth_client.post("/api/auth/unlock", json={})
    assert response.status_code == 428, response.text


def test_unlock_validates_body_after_configuration_guard(auth_client):
    assert auth_client.post("/api/auth/setup", json={"secret": "1234"}).status_code == 200
    response = auth_client.post("/api/auth/unlock", json={"secret": True})
    assert response.status_code == 422, response.text


def test_local_recovery_guard_runs_before_body_validation(auth_client, monkeypatch):
    import api.router_auth as router_auth

    monkeypatch.setattr(router_auth, "_is_loopback", lambda _request: False)
    response = auth_client.post("/api/auth/local-unlock", json={})
    assert response.status_code == 403, response.text


def test_change_secret_rejects_unknown_or_missing_fields(auth_client):
    setup = auth_client.post("/api/auth/setup", json={"secret": "1234"})
    auth_client.headers["X-CSRF-Token"] = setup.json()["csrf_token"]
    auth_client.headers["Origin"] = "http://testserver"

    missing = auth_client.post("/api/auth/change-secret", json={"current": "1234"})
    extra = auth_client.post(
        "/api/auth/change-secret",
        json={"current": "1234", "new": "5678", "extra": True},
    )
    assert missing.status_code == 422, missing.text
    assert extra.status_code == 422, extra.text

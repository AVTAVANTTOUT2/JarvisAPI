"""Validation stricte du pairage, du push et des capacités Android."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mobile_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "mobile-auth-input-contract.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)

    from database import init_db
    from main import app
    from tests.conftest import authenticate

    init_db()
    with TestClient(app) as client:
        authenticate(client)
        yield client


def _pair(client: TestClient, device_id: str = "strict-phone") -> str:
    code = client.post("/api/mobile/pairing/start").json()["code"]
    response = client.post(
        "/api/mobile/pairing/complete",
        json={"code": code, "device_id": device_id, "name": "Galaxy S24"},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"code": "12345", "device_id": "phone"},
        {"code": 123456, "device_id": "phone"},
        {"code": "123456", "device_id": True},
        {"code": "123456", "device_id": "phone", "unexpected": True},
    ],
)
def test_pairing_complete_rejects_invalid_payloads_with_422(mobile_client, payload):
    response = mobile_client.post("/api/mobile/pairing/complete", json=payload)
    assert response.status_code == 422, response.text


def test_push_auth_runs_before_body_validation(mobile_client):
    mobile_client.cookies.clear()
    response = mobile_client.post("/api/mobile/push-token", json={})
    assert response.status_code == 401, response.text


@pytest.mark.parametrize("payload", [{}, {"token": True}, {"token": "ok", "extra": 1}])
def test_push_token_rejects_invalid_payloads_with_422(mobile_client, payload):
    token = _pair(mobile_client)
    response = mobile_client.post(
        "/api/mobile/push-token",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "payload",
    [{}, {"push": None}, {"push": 1}, {"push": True, "unexpected": False}],
)
def test_capabilities_reject_invalid_payloads_with_422(mobile_client, payload):
    token = _pair(mobile_client)
    response = mobile_client.post(
        "/api/mobile/capabilities",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422, response.text


def test_valid_capabilities_are_persisted_without_coercion(mobile_client):
    from database import get_db

    token = _pair(mobile_client)
    response = mobile_client.post(
        "/api/mobile/capabilities",
        json={"push": True, "background_location": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["capabilities"] == {
        "push": True,
        "background_location": False,
    }

    with get_db() as conn:
        raw = conn.execute(
            "SELECT capabilities_json FROM mobile_devices WHERE device_id = ?",
            ("strict-phone",),
        ).fetchone()[0]
    assert json.loads(raw) == {"push": True, "background_location": False}

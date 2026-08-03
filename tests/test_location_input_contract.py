"""Contrats Pydantic et ordre auth → validation de l'ingestion GPS."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def location_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "location-input-contract.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.LOCATION_API_TOKEN", "location-secret")

    from database import init_db
    from main import app

    init_db()
    with TestClient(app) as client:
        yield client


def test_location_auth_runs_before_body_validation(location_client):
    response = location_client.post("/api/location", json={"latitude": "invalide"})
    assert response.status_code == 401, response.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"latitude": "50.6", "longitude": 3.0},
        {"latitude": 50.6, "longitude": 3.0, "accuracy": -1.0},
        {"latitude": 50.6, "longitude": 3.0, "heading": 361.0},
        {"latitude": 50.6, "longitude": 3.0, "unexpected": True},
    ],
)
def test_location_point_rejects_invalid_payloads_with_422(location_client, payload):
    response = location_client.post(
        "/api/location",
        json=payload,
        headers={"X-Location-Token": "location-secret"},
    )
    assert response.status_code == 422, response.text


def test_batch_auth_runs_before_envelope_validation(location_client):
    response = location_client.post("/api/location/batch", json={"unexpected": True})
    assert response.status_code == 401, response.text

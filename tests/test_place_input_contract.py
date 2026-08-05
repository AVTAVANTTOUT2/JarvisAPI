"""Validation des mutations de lieux et de la position courante."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def place_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "place-input-contract.db"
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
        {"name": "Bureau", "latitude": 91.0, "longitude": 2.3},
        {"name": "Bureau", "latitude": 48.8, "longitude": 181.0},
        {"name": True, "latitude": 48.8, "longitude": 2.3},
        {"name": "Bureau", "latitude": 48.8, "longitude": 2.3, "visit_count": 99},
    ],
)
def test_place_creation_rejects_invalid_payloads_with_422(place_client, payload):
    response = place_client.post("/api/places", json=payload)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"visit_count": 99},
        {"latitude": -91.0},
        {"name": None},
        {"radius": 50.0, "radius_meters": 50.0},
    ],
)
def test_place_update_rejects_invalid_or_internal_fields_with_422(
    place_client,
    payload,
):
    response = place_client.put("/api/places/1", json=payload)
    assert response.status_code == 422, response.text


def test_valid_place_payload_is_normalized_and_persisted(place_client):
    created = place_client.post(
        "/api/places",
        json={
            "name": "  Bureau  ",
            "category": "work",
            "latitude": 48.8566,
            "longitude": 2.3522,
        },
    )
    assert created.status_code == 200, created.text
    place = created.json()
    assert place["name"] == "Bureau"

    updated = place_client.put(
        f"/api/places/{place['id']}",
        json={"radius": 150.0},
    )
    assert updated.status_code == 200, updated.text


def test_name_current_without_recent_point_is_a_structured_409(place_client):
    response = place_client.post(
        "/api/location/name-current",
        json={"name": "Maison", "category": "home"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "recent_location_not_found"


def test_name_current_validates_before_reading_location(place_client):
    response = place_client.post(
        "/api/location/name-current",
        json={"name": "", "unexpected": True},
    )
    assert response.status_code == 422, response.text

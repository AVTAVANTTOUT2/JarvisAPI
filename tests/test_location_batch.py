"""Tests batch GPS idempotent — Vague 2B."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.conftest import authenticate  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "location_batch.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.LOCATION_TRACKING", True)
    from database import init_db

    init_db()
    return db_path


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def _pair(client, device_id: str = "s24-loc") -> str:
    start = client.post("/api/mobile/pairing/start")
    assert start.status_code == 200
    code = start.json()["code"]
    complete = client.post(
        "/api/mobile/pairing/complete",
        json={
            "code": code,
            "device_id": device_id,
            "name": "Galaxy S24",
            "model": "SM-S921B",
            "app_version": "2.0.0-alpha",
        },
    )
    assert complete.status_code == 200
    return complete.json()["token"]


def _point(cid: str, lat: float = 50.63, lng: float = 3.06, ts: int | None = None) -> dict:
    if ts is None:
        import time

        ts = int(time.time() * 1000)
    return {
        "client_point_id": cid,
        "latitude": lat,
        "longitude": lng,
        "altitude": 20.0,
        "accuracy": 12.0,
        "speed": 1.0,
        "bearing": 90.0,
        "provider": "gps",
        "captured_at": ts,
        "source": "android_background",
    }


def test_batch_requires_bearer(tmp_db):
    with _client() as client:
        r = client.post("/api/location/batch", json={"points": [_point("pt-aaaaaaa1")]})
    assert r.status_code == 401


def test_batch_rejects_invalid_bearer(tmp_db):
    with _client() as client:
        r = client.post(
            "/api/location/batch",
            json={"points": [_point("pt-aaaaaaa1")]},
            headers={"Authorization": "Bearer deadbeef"},
        )
    assert r.status_code == 401


def test_batch_empty_ok(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        r = client.post(
            "/api/location/batch",
            json={"points": []},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == []
    assert body["duplicates"] == []
    assert body["rejected"] == []


def test_batch_valid_and_idempotent_retry(tmp_db):
    from database.location_helpers import count_location_history

    with _client() as client:
        authenticate(client)
        token = _pair(client, "s24-a")
        headers = {"Authorization": f"Bearer {token}"}
        payload = {"points": [_point("pt-bbbbbbb1"), _point("pt-bbbbbbb2", lat=50.64)]}
        first = client.post("/api/location/batch", json=payload, headers=headers)
        second = client.post("/api/location/batch", json=payload, headers=headers)

    assert first.status_code == 200
    assert set(first.json()["accepted"]) == {"pt-bbbbbbb1", "pt-bbbbbbb2"}
    assert first.json()["duplicates"] == []
    assert second.status_code == 200
    assert second.json()["accepted"] == []
    assert set(second.json()["duplicates"]) == {"pt-bbbbbbb1", "pt-bbbbbbb2"}
    assert count_location_history() == 2


def test_batch_same_uuid_two_devices(tmp_db):
    from database.location_helpers import count_location_history

    with _client() as client:
        authenticate(client)
        t1 = _pair(client, "device-one")
        t2 = _pair(client, "device-two")
        p = {"points": [_point("shared-id-01")]}
        r1 = client.post(
            "/api/location/batch",
            json=p,
            headers={"Authorization": f"Bearer {t1}"},
        )
        r2 = client.post(
            "/api/location/batch",
            json=p,
            headers={"Authorization": f"Bearer {t2}"},
        )

    assert r1.status_code == 200 and r1.json()["accepted"] == ["shared-id-01"]
    assert r2.status_code == 200 and r2.json()["accepted"] == ["shared-id-01"]
    assert count_location_history() == 2


def test_batch_too_large(tmp_db, monkeypatch):
    monkeypatch.setattr("config.LOCATION_BATCH_MAX_POINTS", 2)
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        points = [_point(f"pt-ccccccc{i}") for i in range(3)]
        r = client.post(
            "/api/location/batch",
            json={"points": points},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 400
    assert "trop grand" in r.json()["detail"].lower()


def test_batch_invalid_coordinates(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        bad = _point("pt-ddddddd1", lat=999.0)
        r = client.post(
            "/api/location/batch",
            json={"points": [bad]},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == []
    assert body["rejected"][0]["reason"] == "invalid_coordinates"


def test_batch_partial_response(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        points = [
            _point("pt-eeeeeee1"),
            _point("pt-eeeeeee2", lat=999.0),
            {"latitude": 50.0, "longitude": 3.0},  # missing client_point_id
        ]
        r = client.post(
            "/api/location/batch",
            json={"points": points},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == ["pt-eeeeeee1"]
    reasons = {x["client_point_id"]: x["reason"] for x in body["rejected"]}
    assert reasons["pt-eeeeeee2"] == "invalid_coordinates"
    assert "missing_client_point_id" in reasons.values()


def test_batch_revoked_device(tmp_db):
    from database import revoke_mobile_device

    with _client() as client:
        authenticate(client)
        token = _pair(client, "s24-revoked")
        revoke_mobile_device("s24-revoked")
        r = client.post(
            "/api/location/batch",
            json={"points": [_point("pt-fffffff1")]},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 401


def test_unit_location_still_works_without_client_id(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        r = client.post(
            "/api/location",
            json={
                "latitude": 50.63,
                "longitude": 3.06,
                "accuracy": 10,
                "source": "android_background",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200

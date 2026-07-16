"""Tests name_place et diagnostics localisation mobile."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "name_place.db"
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


def _pair(client, device_id: str = "s24-diag") -> str:
    from tests.conftest import authenticate

    authenticate(client)
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
            "app_version": "2.0.0-alpha03",
        },
    )
    assert complete.status_code == 200
    return complete.json()["token"]


@pytest.mark.asyncio
async def test_name_place_refuses_without_location(tmp_db):
    import actions
    from database import get_db

    result = await actions._action_name_place({"name": "maison", "category": "home"})
    assert result["ok"] is False
    assert "aucune position récente" in result["message"].lower()
    assert result.get("code") == "NO_RECENT_LOCATION"
    with get_db() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM places").fetchone()["n"]
    assert n == 0


@pytest.mark.asyncio
async def test_name_place_creates_when_recent_location(tmp_db):
    import actions
    from database import get_db

    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as conn:
        conn.execute(
            """INSERT INTO location_history
               (latitude, longitude, accuracy, source, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (50.63, 3.06, 12.0, "android_background", now),
        )
        conn.commit()

    result = await actions._action_name_place({"name": "maison", "category": "home"})
    assert result["ok"] is True
    assert result.get("place_id")
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM places WHERE id = ?",
            (result["place_id"],),
        ).fetchone()
    assert row is not None
    assert float(row["latitude"]) == pytest.approx(50.63)
    assert float(row["longitude"]) == pytest.approx(3.06)


@pytest.mark.asyncio
async def test_name_place_rejects_stale_location(tmp_db):
    import actions

    stale = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
    from database import get_db

    with get_db() as conn:
        conn.execute(
            """INSERT INTO location_history
               (latitude, longitude, accuracy, source, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (50.63, 3.06, 12.0, "android_background", stale),
        )
        conn.commit()

    result = await actions._action_name_place({"name": "maison", "category": "home"})
    assert result["ok"] is False
    assert result.get("code") == "NO_RECENT_LOCATION"


def test_mobile_location_diagnostics(tmp_db):
    with _client() as client:
        token = _pair(client)
        empty = client.get(
            "/api/mobile/location/diagnostics",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert empty.status_code == 200
        body = empty.json()
        assert body["points_received_24h"] == 0
        assert body["last_point_received_at"] is None

        import time

        batch = client.post(
            "/api/location/batch",
            json={
                "points": [
                    {
                        "client_point_id": "diagpt001",
                        "latitude": 50.63,
                        "longitude": 3.06,
                        "accuracy": 10.0,
                        "provider": "gps",
                        "captured_at": int(time.time() * 1000),
                        "source": "android_background",
                    }
                ]
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert batch.status_code == 200
        assert "diagpt001" in batch.json()["accepted"]

        diag = client.get(
            "/api/mobile/location/diagnostics",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert diag.status_code == 200
        d = diag.json()
        assert d["points_received_24h"] >= 1
        assert d["last_point_received_at"] is not None
        assert d["device_id"] == "s24-diag"


def test_location_history_endpoint_returns_empty_points(tmp_db):
    from tests.conftest import authenticate

    with _client() as client:
        authenticate(client)
        response = client.get("/api/location/history?hours=24")

    assert response.status_code == 200
    assert response.json() == {"points": []}


def test_location_history_endpoint_exposes_android_point_when_tracking_disabled(
    tmp_db,
    monkeypatch: pytest.MonkeyPatch,
):
    from database import get_db
    from tests.conftest import authenticate

    monkeypatch.setattr("config.LOCATION_TRACKING", False)
    captured_at = datetime.now().isoformat(timespec="seconds")
    with get_db() as conn:
        conn.execute(
            """INSERT INTO location_history
               (latitude, longitude, accuracy, source, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (50.63, 3.06, 12.0, "android_background", captured_at),
        )

    with _client() as client:
        authenticate(client)
        response = client.get("/api/location/history?hours=24")

    assert response.status_code == 200
    point = response.json()["points"][0]
    assert point["latitude"] == pytest.approx(50.63)
    assert point["longitude"] == pytest.approx(3.06)
    assert point["accuracy"] == pytest.approx(12.0)
    assert point["source"] == "android_background"
    assert point["created_at"] == captured_at


def test_location_status_returns_last_known_even_when_stale_and_tracking_disabled(
    tmp_db,
    monkeypatch: pytest.MonkeyPatch,
):
    """Le frontend ne doit pas voir current_location=null si l'historique a un point >10 min."""
    from database import get_db
    from tests.conftest import authenticate

    monkeypatch.setattr("config.LOCATION_TRACKING", False)
    stale_at = (datetime.now() - timedelta(minutes=25)).isoformat(timespec="seconds")
    with get_db() as conn:
        conn.execute(
            """INSERT INTO location_history
               (latitude, longitude, accuracy, source, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (50.63, 3.06, 12.0, "android_background", stale_at),
        )

    with _client() as client:
        authenticate(client)
        response = client.get("/api/location/status")

    assert response.status_code == 200
    body = response.json()
    assert body["tracking_enabled"] is False
    assert body["points_24h"] >= 1
    cur = body["current_location"]
    assert cur is not None
    assert cur["source"] == "android_background"
    assert cur["created_at"] == stale_at
    assert cur["latitude"] == pytest.approx(50.63)
    assert cur["longitude"] == pytest.approx(3.06)


def test_get_current_location_still_requires_recent_point_for_name_place(tmp_db):
    from database.location_helpers import get_current_location, get_last_known_location
    from database import get_db

    stale_at = (datetime.now() - timedelta(minutes=25)).isoformat(timespec="seconds")
    with get_db() as conn:
        conn.execute(
            """INSERT INTO location_history
               (latitude, longitude, accuracy, source, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (50.63, 3.06, 12.0, "android_background", stale_at),
        )

    assert get_last_known_location() is not None
    assert get_current_location() is None

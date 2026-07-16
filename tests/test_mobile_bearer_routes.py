"""Bearer mobile : accès lecture aux routes métier sans cookie session."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.conftest import authenticate  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "mobile_bearer.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def _pair(client, device_id: str = "bearer-phone") -> str:
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
            "app_version": "1.2.0",
        },
    )
    assert complete.status_code == 200
    return complete.json()["token"]


def test_bearer_can_read_tasks_without_cookie(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()

        response = client.get(
            "/api/tasks",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert "tasks" in response.json()


def test_bearer_can_read_briefing_notifications_calendar(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()
        headers = {"Authorization": f"Bearer {token}"}

        briefing = client.get("/api/briefing", headers=headers)
        notifications = client.get("/api/notifications", headers=headers)
        calendar = client.get(
            "/api/calendar",
            params={"start": "2026-07-16T00:00:00", "end": "2026-07-17T00:00:00"},
            headers=headers,
        )
        conversations = client.get("/api/conversations", headers=headers)

    # Briefing may 500 if LLM unavailable — auth must not be 401.
    assert briefing.status_code != 401
    assert briefing.status_code != 428
    assert notifications.status_code == 200
    assert "notifications" in notifications.json()
    # Calendar may 503 if Calendar.app down — auth gate must pass.
    assert calendar.status_code != 401
    assert calendar.status_code != 428
    assert conversations.status_code == 200
    assert "conversations" in conversations.json()


def test_revoked_bearer_rejected_on_tasks(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client, device_id="revoke-me")
        client.cookies.clear()
        authenticate(client)
        revoke = client.post("/api/mobile/devices/revoke-me/revoke")
        assert revoke.status_code == 200
        client.cookies.clear()

        response = client.get(
            "/api/tasks",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 401


def test_cookie_session_still_reads_tasks_without_bearer(tmp_db):
    with _client() as client:
        authenticate(client)
        response = client.get("/api/tasks")

    assert response.status_code == 200
    assert "tasks" in response.json()


def test_tasks_without_auth_unauthorized(tmp_db):
    with _client() as client:
        authenticate(client)  # configure PIN
        client.cookies.clear()
        response = client.get("/api/tasks")

    assert response.status_code == 401
    assert response.json().get("error") == "unauthorized"


def test_bearer_does_not_open_mutations_in_wave1(tmp_db):
    """Vague 1 : POST métier reste refusé sans cookie session."""
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        client.cookies.clear()

        response = client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Should fail"},
        )

    assert response.status_code == 401

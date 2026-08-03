"""Validation stricte des mutations Calendar et Relations."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def relationships_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "relationships-input-contract.db"
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
        {"title": "Réunion", "start": "demain 14h"},
        {"title": "Réunion", "start": "2026-08-03T15:00", "end": "2026-08-03T14:00"},
        {"title": "Réunion", "start": True},
        {"title": "Réunion", "start": "2026-08-03T15:00", "extra": 1},
    ],
)
def test_calendar_create_rejects_invalid_payloads(relationships_client, payload):
    response = relationships_client.post("/api/calendar", json=payload)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": ""},
        {"name": True},
        {"name": "Bertille", "extra": 1},
    ],
)
def test_analyze_contact_rejects_invalid_payloads(relationships_client, payload):
    response = relationships_client.post("/api/analyze-contact", json=payload)
    assert response.status_code == 422, response.text


class _AvailableCalendar:
    def __init__(self) -> None:
        self.create_event = AsyncMock(return_value={"ok": True, "summary": "Réunion"})

    @staticmethod
    def is_available() -> bool:
        return True


def test_calendar_create_accepts_summary_alias_and_normalizes_text(
    relationships_client,
    monkeypatch: pytest.MonkeyPatch,
):
    calendar = _AvailableCalendar()
    monkeypatch.setattr("api.misc_relationships.calendar_client", calendar)

    response = relationships_client.post(
        "/api/calendar",
        json={
            "summary": "  Réunion  ",
            "start": "2026-08-03T15:00",
            "end": "2026-08-03T16:00",
            "location": "  Bureau  ",
        },
    )

    assert response.status_code == 200, response.text
    calendar.create_event.assert_awaited_once_with(
        summary="Réunion",
        start_date="2026-08-03T15:00",
        end_date="2026-08-03T16:00",
        calendar_name=None,
        location="Bureau",
        notes="",
    )


def test_analyze_contact_passes_trimmed_name(relationships_client):
    analyze = AsyncMock(return_value={"summary": "Relation stable"})
    with patch("scripts.relationship_analyzer.analyzer.analyze_single_contact", analyze):
        response = relationships_client.post(
            "/api/analyze-contact",
            json={"name": "  Bertille  "},
        )

    assert response.status_code == 200, response.text
    analyze.assert_awaited_once_with("Bertille")

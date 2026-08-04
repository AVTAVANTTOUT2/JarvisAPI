"""Tests timeline relationnelle (parsing JSON LLM + cache vide)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.conftest import authenticate


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    import database

    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    return db_path


def test_parse_events_json_recovers_complete_objects_from_truncated_array():
    from scripts.timeline_generator import _parse_events_json

    truncated = (
        '[\n'
        '    {"date": "2026-03-07", "type": "first_contact", '
        '"title": "Prise de nouvelles", '
        '"summary": "Maman demande comment va son fils."},\n'
        '    {"date": "2026-03-09", "type": "support", '
        '"title": "Inquiétude", '
        '"summary": "Propose son aide."},\n'
        '    {"date": "2026-03-10", "type": "deep_con'
    )
    events = _parse_events_json(truncated)
    assert len(events) == 2
    assert events[0]["title"] == "Prise de nouvelles"
    assert events[1]["type"] == "support"


def test_parse_events_json_accepts_complete_array():
    from scripts.timeline_generator import _parse_events_json

    content = (
        '[{"date": "2026-01-01", "type": "milestone", '
        '"title": "Anniversaire", "summary": "Fêté ensemble."}]'
    )
    events = _parse_events_json(content)
    assert len(events) == 1
    assert events[0]["date"] == "2026-01-01"


def test_get_timeline_regenerates_when_cache_is_empty_list(tmp_db, monkeypatch):
    """Un cache \"[]\" ne doit pas bloquer la régénération au GET."""
    from database import upsert_person, update_person_timeline_cache

    upsert_person("Maman", relationship="famille")
    update_person_timeline_cache("Maman", [])

    fake_events = [
        {
            "date": "2026-03-07",
            "type": "support",
            "title": "Prise de nouvelles",
            "summary": "Maman s'inquiète.",
        }
    ]
    monkeypatch.setattr(
        "scripts.timeline_generator.generate_timeline",
        AsyncMock(return_value=fake_events),
    )
    monkeypatch.setattr(
        "api.router_people._resolve_handle_with_contacts",
        lambda _name: "+33600000000",
    )

    import main

    with TestClient(main.app) as client:
        authenticate(client)
        r = client.get("/api/people/Maman/timeline")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["events"] == fake_events
        assert body["from_cache"] is False


def test_get_timeline_serves_non_empty_cache(tmp_db, monkeypatch):
    from database import upsert_person, update_person_timeline_cache

    events = [
        {
            "date": "2026-01-01",
            "type": "milestone",
            "title": "Premier café",
            "summary": "Première vraie discussion.",
        }
    ]
    upsert_person("Maman", relationship="famille")
    update_person_timeline_cache("Maman", events)

    called = AsyncMock(return_value=[])
    monkeypatch.setattr("scripts.timeline_generator.generate_timeline", called)

    import main

    with TestClient(main.app) as client:
        authenticate(client)
        r = client.get("/api/people/Maman/timeline")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["events"] == events
        assert body["from_cache"] is True
        called.assert_not_called()


def test_people_timeline_does_not_expose_internal_exception(tmp_db, monkeypatch):
    from database import upsert_person

    upsert_person("Maman", relationship="famille")
    monkeypatch.setattr(
        "scripts.timeline_generator.generate_timeline",
        AsyncMock(side_effect=RuntimeError("sqlite secret path /Users/private/jarvis.db")),
    )

    import main

    with TestClient(main.app) as client:
        authenticate(client)
        response = client.get("/api/people/Maman/timeline")

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "people_timeline_failed",
        "message": "Timeline du contact indisponible",
    }
    assert "sqlite" not in response.text.lower()
    assert "/users/private" not in response.text.lower()


def test_people_patch_rejects_unknown_fields_with_pydantic_contract(tmp_db):
    from database import upsert_person

    upsert_person("Maman", relationship="famille")

    import main

    with TestClient(main.app) as client:
        authenticate(client)
        response = client.patch(
            "/api/people/Maman",
            json={"relationship": "famille", "admin": True},
        )

    assert response.status_code == 422

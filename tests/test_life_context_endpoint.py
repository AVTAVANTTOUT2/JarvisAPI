"""Tests : endpoint /api/life-context (périodes de vie détectées)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def test_get_life_context_empty(tmp_db):
    with _client() as client:
        r = client.get("/api/life-context")
    assert r.status_code == 200
    assert r.json()["periods"] == []


def test_create_and_list_life_context(tmp_db):
    with _client() as client:
        r = client.post("/api/life-context", json={
            "context_type": "demenagement", "description": "Déménagement à Lille",
        })
        assert r.status_code == 200
        context_id = r.json()["id"]

        r2 = client.get("/api/life-context")
    periods = r2.json()["periods"]
    assert len(periods) == 1
    assert periods[0]["id"] == context_id
    assert periods[0]["description"] == "Déménagement à Lille"


def test_create_requires_type_and_description(tmp_db):
    with _client() as client:
        r = client.post("/api/life-context", json={"context_type": "", "description": ""})
    assert r.status_code == 400


def test_active_only_filters_closed_periods(tmp_db):
    with _client() as client:
        r1 = client.post("/api/life-context", json={
            "context_type": "travail", "description": "Nouveau travail",
        })
        cid = r1.json()["id"]
        client.post(f"/api/life-context/{cid}/close")

        r2 = client.post("/api/life-context", json={
            "context_type": "relation", "description": "Nouvelle relation",
        })

        active = client.get("/api/life-context?active_only=true").json()["periods"]
        full = client.get("/api/life-context").json()["periods"]

    assert len(active) == 1
    assert active[0]["id"] == r2.json()["id"]
    assert len(full) == 2


def test_close_unknown_context_404(tmp_db):
    with _client() as client:
        r = client.post("/api/life-context/999/close")
    assert r.status_code == 404

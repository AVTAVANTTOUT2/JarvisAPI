"""Tests : endpoints REST du lot 1 (prédiction, lieux, doomscroll, procrastination,
journal JARVIS, jours exceptionnels/chance, cohérence promesses)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

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


def test_predictions_messages_endpoint_empty(tmp_db):
    with _client() as client:
        r = client.get("/api/predictions/messages")
    assert r.status_code == 200
    assert r.json()["predictions"] == []


def test_places_favorites_endpoint(tmp_db):
    from database import get_db

    with get_db() as conn:
        conn.execute(
            "INSERT INTO places (name, category, latitude, longitude, visit_count) "
            "VALUES ('Salle de sport', 'gym', 1, 1, 10)"
        )

    with _client() as client:
        r = client.get("/api/places/favorites")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()["places"]]
    assert "Salle de sport" in names


def test_places_missed_opportunities_endpoint(tmp_db):
    with _client() as client:
        r = client.get("/api/places/missed-opportunities")
    assert r.status_code == 200
    assert r.json()["opportunities"] == []


def test_doomscroll_endpoint(tmp_db):
    with _client() as client:
        r = client.get("/api/doomscroll")
    assert r.status_code == 200
    assert r.json()["days"] == []


def test_procrastination_cost_endpoint(tmp_db):
    with _client() as client:
        r = client.get("/api/procrastination/cost")
    assert r.status_code == 200
    body = r.json()
    assert body["abandoned_tasks"] == []


def test_jarvis_journal_get_endpoint(tmp_db):
    from database import upsert_jarvis_journal_entry

    upsert_jarvis_journal_entry("2026-07-10", "Journée sans histoire.")
    with _client() as client:
        r = client.get("/api/jarvis-journal?days=7")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert entries[0]["entry"] == "Journée sans histoire."


def test_jarvis_journal_generate_endpoint(tmp_db):
    fake_result = {"content": "Monsieur a bien travaillé.", "tokens_in": 1, "tokens_out": 1,
                    "cache_hit": 0, "cost": 0.0, "model": "test", "stop_reason": "stop"}
    with patch("llm.chat", new=AsyncMock(return_value=fake_result)):
        with _client() as client:
            r = client.post("/api/jarvis-journal/generate", json={"date": "2026-07-10"})
    assert r.status_code == 200
    assert r.json()["entry"] == "Monsieur a bien travaillé."


def test_day_scores_top_endpoint(tmp_db):
    from database import upsert_day_score

    upsert_day_score("2026-07-10", exceptional_score=90, luck_score=40, factors={})
    with _client() as client:
        r = client.get("/api/day-scores?metric=exceptional_score&limit=5")
    assert r.status_code == 200
    assert r.json()["days"][0]["date"] == "2026-07-10"


def test_day_scores_invalid_metric_400(tmp_db):
    with _client() as client:
        r = client.get("/api/day-scores?metric=nonsense")
    assert r.status_code == 400


def test_day_score_detail_endpoint(tmp_db):
    from database import upsert_day_score

    upsert_day_score("2026-07-10", exceptional_score=90, luck_score=40, factors={})
    with _client() as client:
        r = client.get("/api/day-scores/2026-07-10")
    assert r.status_code == 200
    assert r.json()["exceptional_score"] == 90


def test_day_score_detail_missing_404(tmp_db):
    with _client() as client:
        r = client.get("/api/day-scores/2020-01-01")
    assert r.status_code == 404


def test_commitments_consistency_endpoint(tmp_db):
    from database import add_commitment, update_commitment_status

    cid = add_commitment("Envoyer le rapport")
    update_commitment_status(cid, "kept")
    with _client() as client:
        r = client.get("/api/commitments/consistency")
    assert r.status_code == 200
    assert r.json()["score"] == 100

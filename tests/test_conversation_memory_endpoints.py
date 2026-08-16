"""Tests : endpoints REST diarisation (tours/locuteurs) + recherche sémantique."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.conftest import authenticate  # noqa: E402


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


def _make_recording() -> int:
    from database import save_recording

    return save_recording(
        conversation_id=None,
        label="test",
        duration_seconds=60,
        transcription="Bonjour. Salut.",
        summary="Résumé",
        synthesis={},
        actions={},
        audio_size_kb=1,
    )


# ── Tours de parole / locuteurs ─────────────────────────────────


def test_get_turns_for_recording(tmp_db):
    from database import save_conversation_turns

    rec_id = _make_recording()
    save_conversation_turns(
        rec_id,
        [
            {"speaker_label": "A", "text": "Bonjour", "start_ms": 0, "end_ms": 500},
            {"speaker_label": "B", "text": "Salut", "start_ms": 600, "end_ms": 900},
        ],
    )

    with _client() as client:
        authenticate(client)
        r = client.get(f"/api/recordings/{rec_id}/turns")
    assert r.status_code == 200
    turns = r.json()["turns"]
    assert len(turns) == 2
    assert turns[0]["speaker_label"] == "A"


def test_get_turns_unknown_recording_404(tmp_db):
    with _client() as client:
        authenticate(client)
        r = client.get("/api/recordings/999999/turns")
    assert r.status_code == 404


def test_get_unlabeled_speakers(tmp_db):
    from database import save_conversation_turns

    rec_id = _make_recording()
    save_conversation_turns(
        rec_id,
        [
            {"speaker_label": "A", "text": "un", "start_ms": 0, "end_ms": 100},
            {"speaker_label": "B", "text": "deux", "start_ms": 100, "end_ms": 200},
        ],
    )

    with _client() as client:
        authenticate(client)
        r = client.get(f"/api/recordings/{rec_id}/speakers")
    assert r.status_code == 200
    assert r.json()["unlabeled_speakers"] == ["A", "B"]


def test_assign_speaker_creates_new_person(tmp_db):
    from database import get_conversation_turns, save_conversation_turns

    rec_id = _make_recording()
    save_conversation_turns(
        rec_id,
        [{"speaker_label": "A", "text": "Bonjour", "start_ms": 0, "end_ms": 100}],
    )

    with _client() as client:
        authenticate(client)
        r = client.post(
            f"/api/recordings/{rec_id}/speakers/A/assign", json={"name": "Karim"}
        )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Karim"
    assert body["turns_updated"] == 1

    turns = get_conversation_turns(rec_id)
    assert turns[0]["person_name"] == "Karim"


def test_assign_speaker_reuses_existing_person(tmp_db):
    from database import get_db, save_conversation_turns

    with get_db() as conn:
        cur = conn.execute("INSERT INTO people (name) VALUES ('Karim')")
        existing_id = cur.lastrowid

    rec_id = _make_recording()
    save_conversation_turns(
        rec_id, [{"speaker_label": "A", "text": "x", "start_ms": 0, "end_ms": 100}]
    )

    with _client() as client:
        authenticate(client)
        r = client.post(
            f"/api/recordings/{rec_id}/speakers/A/assign", json={"name": "Karim"}
        )
    assert r.json()["person_id"] == existing_id


def test_assign_speaker_missing_name_422(tmp_db):
    rec_id = _make_recording()
    with _client() as client:
        authenticate(client)
        r = client.post(f"/api/recordings/{rec_id}/speakers/A/assign", json={})
    assert r.status_code == 422


def test_assign_speaker_rejects_unknown_and_coerced_fields(tmp_db):
    rec_id = _make_recording()
    with _client() as client:
        authenticate(client)
        coerced = client.post(
            f"/api/recordings/{rec_id}/speakers/A/assign",
            json={"name": 123},
        )
        unknown = client.post(
            f"/api/recordings/{rec_id}/speakers/A/assign",
            json={"name": "Karim", "person_id": 42},
        )
    assert coerced.status_code == 422
    assert unknown.status_code == 422


def test_assign_unknown_label_404(tmp_db):
    from database import save_conversation_turns

    rec_id = _make_recording()
    save_conversation_turns(
        rec_id, [{"speaker_label": "A", "text": "x", "start_ms": 0, "end_ms": 100}]
    )

    with _client() as client:
        authenticate(client)
        r = client.post(
            f"/api/recordings/{rec_id}/speakers/Z/assign", json={"name": "Karim"}
        )
    assert r.status_code == 404


# ── Recherche sémantique ────────────────────────────────────────


def test_semantic_search_endpoint(tmp_db):
    from database import get_db

    with get_db() as conn:
        conn.execute(
            "INSERT INTO episodes (id, agent, content, summary) VALUES (?, ?, ?, ?)",
            (1, "memory", "Jean parle de ses vacances en Espagne", "Vacances"),
        )

    with _client() as client:
        authenticate(client)
        r = client.get("/api/memory/search-semantic?q=vacances")
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 1
    assert results[0]["source_id"] == 1
    assert set(results[0]) == {"source_type", "source_id", "text_preview", "score"}


def test_semantic_search_missing_query_400(tmp_db):
    with _client() as client:
        authenticate(client)
        r = client.get("/api/memory/search-semantic?q=")
    assert r.status_code == 400


def test_semantic_search_unavailable_returns_503(tmp_db):
    from jarvis.retrieval import RetrievalResult

    with patch(
        "jarvis.retrieval.search_knowledge",
        return_value=RetrievalResult(status="unavailable", query="test"),
    ):
        with _client() as client:
            authenticate(client)
            r = client.get("/api/memory/search-semantic?q=test")
    assert r.status_code == 503


def test_semantic_search_delegates_and_keeps_legacy_result_shape(tmp_db):
    from jarvis.retrieval import RetrievalHit, RetrievalResult

    captured = []

    def fake_search(request):
        captured.append(request)
        return RetrievalResult(
            status="ok",
            query=request.query,
            hits=(
                RetrievalHit(
                    uid="recording:7",
                    source_type="recording",
                    source_id="7",
                    title="Réunion",
                    excerpt="Décision de la réunion",
                    score=0.87654,
                ),
            ),
            verified_sources=("recording",),
        )

    with patch("jarvis.retrieval.search_knowledge", side_effect=fake_search):
        with _client() as client:
            authenticate(client)
            response = client.get(
                "/api/memory/search-semantic?q=décision&limit=20&source_type=recording"
            )

    assert response.status_code == 200
    assert response.json() == {
        "results": [
            {
                "source_type": "recording",
                "source_id": 7,
                "text_preview": "Décision de la réunion",
                "score": 0.8765,
            }
        ]
    }
    assert captured[0].query == "décision"
    assert captured[0].source_types == ("recording",)
    assert captured[0].max_hits == 8
    assert captured[0].interaction_mode == "legacy_semantic_api"


def test_semantic_search_keeps_legacy_episode_recording_boundary(tmp_db):
    with patch("jarvis.retrieval.search_knowledge") as search:
        with _client() as client:
            authenticate(client)
            response = client.get(
                "/api/memory/search-semantic?q=message&source_type=email"
            )

    assert response.status_code == 200
    assert response.json() == {"results": []}
    search.assert_not_called()

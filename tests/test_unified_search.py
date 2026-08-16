"""Tests du contrat de recherche multi-domaines exposé au frontend."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "unified-search.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def _seed() -> None:
    from database import get_db

    with get_db() as conn:
        conn.execute(
            "INSERT INTO conversations (id, agent, title) VALUES (1, 'orchestrator', 'Projet Atlas')"
        )
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (1, 'user', 'jalon alpha')"
        )
        conn.execute(
            "INSERT INTO people (id, name, relationship, ai_description) VALUES (1, 'Alice', 'collègue Atlas', 'pilote alpha')"
        )
        conn.execute(
            "INSERT INTO tasks (id, title, description, priority) VALUES (1, 'alpha', 'Préparer Atlas', 'high')"
        )
        conn.execute(
            "INSERT INTO school_documents (id, title, content, doc_type) VALUES (?, ?, ?, ?)",
            (1, "Notes Atlas", f"{'contexte ' * 40}alpha final", "note"),
        )
        conn.execute(
            "INSERT INTO episodes (id, agent, content, summary) VALUES (1, 'memory', 'décision alpha', 'Décision Atlas')"
        )
        conn.execute(
            "INSERT INTO user_facts (id, category, content) VALUES (1, 'projet', 'Atlas utilise alpha')"
        )


def test_unified_search_covers_every_frontend_category(tmp_db):
    from database import unified_search

    _seed()
    results = unified_search("alpha", limit=30)

    assert {result["category"] for result in results} == {
        "conversations",
        "contacts",
        "tasks",
        "documents",
        "memory",
    }
    conversation = next(
        result for result in results if result["type"] == "conversation"
    )
    document = next(result for result in results if result["type"] == "document")
    assert conversation["checkpoint_id"]
    assert conversation["url"] == "/chat?conversation=1"
    assert "alpha final" in document["subtitle"]
    assert document["subtitle"].startswith("…")
    assert results[0]["type"] == "task"  # correspondance exacte du titre


def test_unified_search_escapes_sql_wildcards_and_clamps_limits(tmp_db):
    from database import unified_search

    _seed()
    assert unified_search("%%") == []
    assert len(unified_search("alpha", limit=1)) == 1


def test_unified_search_delegates_and_preserves_frontend_shape(
    tmp_db, monkeypatch: pytest.MonkeyPatch
):
    import jarvis.retrieval as retrieval_module
    from database import unified_search
    from jarvis.retrieval import RetrievalHit, RetrievalResult

    captured = []

    def fake_search(request):
        captured.append(request)
        return RetrievalResult(
            status="ok",
            query=request.query,
            hits=(
                RetrievalHit(
                    uid="task:7",
                    source_type="task",
                    source_id="7",
                    title="alpha",
                    excerpt="Préparer Atlas",
                    score=16.0,
                    metadata={
                        "status": "todo",
                        "priority": "high",
                        "due_at": "2026-08-20",
                    },
                ),
            ),
            verified_sources=("task",),
        )

    monkeypatch.setattr(retrieval_module, "search_knowledge", fake_search)

    assert unified_search("alpha", limit=50) == [
        {
            "type": "task",
            "category": "tasks",
            "id": 7,
            "title": "alpha",
            "subtitle": "Préparer Atlas",
            "meta": "todo · high · 2026-08-20",
            "url": "/tasks?task=7",
            "score": 110,
        }
    ]
    assert captured[0].query == "alpha"
    assert set(captured[0].source_types) == {
        "conversation",
        "message",
        "person",
        "task",
        "document",
        "episode",
        "fact",
    }
    assert captured[0].max_hits == 8
    assert captured[0].interaction_mode == "legacy_unified_search"


def test_api_search_returns_counts_for_the_frontend(tmp_db):
    from api.misc_relationships import api_search

    _seed()
    response = asyncio.run(api_search("Atlas", limit=30))

    assert response["total"] == len(response["results"])
    assert response["categories"]["conversations"] == 1
    assert response["categories"]["contacts"] == 1
    assert response["categories"]["documents"] == 1

"""Tests de la recherche plein-texte des conversations (FTS5 + fallback LIKE)."""

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


def _seed(conn) -> None:
    conn.execute("INSERT INTO conversations (id, agent, title) VALUES (1, 'orchestrator', 'Révisions droit')")
    conn.execute("INSERT INTO conversations (id, agent, title) VALUES (2, 'orchestrator', 'Recettes cuisine')")
    msgs = [
        (1, "user", "Parle-moi de l'école de commerce", "2026-07-01 10:00:00"),
        (1, "assistant", "L'école propose plusieurs filières.", "2026-07-01 10:00:05"),
        (1, "user", "Et les examens de droit ?", "2026-07-02 09:00:00"),
        (2, "user", "Une recette de pâtes carbonara", "2026-07-03 12:00:00"),
    ]
    for conv, role, content, ts in msgs:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (conv, role, content, ts),
        )


def test_fts_index_created_and_synced(tmp_db):
    from database import get_db

    with get_db() as conn:
        _seed(conn)
        n = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
        assert n == 4  # triggers d'insertion


def test_search_dedupes_by_conversation(tmp_db):
    from database import get_db, search_conversations

    with get_db() as conn:
        _seed(conn)
    # 'école' apparaît dans 2 messages de la conversation 1 → un seul résultat
    results = search_conversations("école")
    ids = [r["id"] for r in results]
    assert ids == [1]
    # le message correspondant retourné est le plus récent
    assert results[0]["match_date"].startswith("2026-07-01 10:00:05")


def test_search_accent_insensitive_and_prefix(tmp_db):
    from database import get_db, search_conversations

    with get_db() as conn:
        _seed(conn)
    # sans accent → trouve 'école' ; préfixe → 'carbo' trouve 'carbonara'
    assert [r["id"] for r in search_conversations("ecole")] == [1]
    assert [r["id"] for r in search_conversations("carbo")] == [2]


def test_search_title_match_included(tmp_db):
    from database import get_db, search_conversations

    with get_db() as conn:
        _seed(conn)
    # 'cuisine' n'est que dans le titre de la conv 2
    assert 2 in [r["id"] for r in search_conversations("cuisine")]


def test_search_special_chars_do_not_crash(tmp_db):
    from database import get_db, search_conversations

    with get_db() as conn:
        _seed(conn)
    for q in ['l\'école (test) "quoted"', "AND OR NOT", "***", "  ", ""]:
        search_conversations(q)  # ne doit pas lever


def test_search_fallback_like_when_fts_missing(tmp_db):
    from database import get_db, search_conversations

    with get_db() as conn:
        _seed(conn)
        conn.executescript("""
            DROP TRIGGER messages_fts_ai; DROP TRIGGER messages_fts_ad;
            DROP TRIGGER messages_fts_au; DROP TABLE messages_fts;
        """)
    results = search_conversations("école")
    assert [r["id"] for r in results] == [1]


def test_delete_message_updates_index(tmp_db):
    from database import get_db, search_conversations

    with get_db() as conn:
        _seed(conn)
        conn.execute("DELETE FROM messages WHERE conversation_id = 2")
    assert search_conversations("carbonara") == []

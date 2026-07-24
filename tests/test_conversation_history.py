"""Contrats de sélection de l'historique récent des conversations."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "conversation-history.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)

    from database import init_db

    init_db()
    return db_path


def _seed_messages(messages: list[tuple[str, str]]) -> None:
    from database import get_db

    with get_db() as conn:
        conn.execute(
            "INSERT INTO conversations (id, agent) VALUES (1, 'orchestrator')"
        )
        conn.executemany(
            """INSERT INTO messages
               (conversation_id, role, content, created_at)
               VALUES (1, 'user', ?, ?)""",
            messages,
        )


def test_history_returns_latest_window_in_chronological_order(tmp_db):
    from database import get_conversation_history

    _seed_messages(
        [
            (f"message-{index}", f"2026-07-24 10:00:{index:02d}")
            for index in range(60)
        ]
    )

    history = get_conversation_history(1)

    assert [message["content"] for message in history] == [
        f"message-{index}" for index in range(10, 60)
    ]


def test_history_uses_message_id_to_order_equal_timestamps(tmp_db):
    from database import get_conversation_history

    timestamp = "2026-07-24 10:00:00"
    _seed_messages([(f"message-{index}", timestamp) for index in range(5)])

    history = get_conversation_history(1, limit=2)

    assert [message["content"] for message in history] == [
        "message-3",
        "message-4",
    ]

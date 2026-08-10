"""Régressions du cycle checkpoint, reprise et titrage des conversations."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def checkpoint_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "conversation_checkpoints.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def test_checkpoint_is_unique_searchable_and_resumable(checkpoint_db):
    from database import (
        create_conversation,
        get_conversation_by_checkpoint,
        get_conversation_detail,
        resolve_conversation_checkpoint,
        save_message,
        search_conversations,
    )

    conversation_id = create_conversation(agent="orchestrator")
    detail = get_conversation_detail(conversation_id)
    checkpoint_id = detail["checkpoint_id"]
    assert str(uuid.UUID(checkpoint_id)) == checkpoint_id

    resolved_id, resumed = resolve_conversation_checkpoint(
        checkpoint_id,
        create=False,
    )
    assert (resolved_id, resumed) == (conversation_id, True)
    assert get_conversation_by_checkpoint(checkpoint_id)["id"] == conversation_id

    save_message(conversation_id, "user", "Diagnostic checkpoint persistant")
    result = search_conversations("checkpoint")[0]
    assert result["checkpoint_id"] == checkpoint_id
    assert result["title_status"] == "pending"

    with pytest.raises(LookupError):
        resolve_conversation_checkpoint(str(uuid.uuid4()), create=False)


def test_requested_checkpoint_creation_is_idempotent(checkpoint_db):
    from database import resolve_conversation_checkpoint

    checkpoint_id = str(uuid.uuid4())
    first_id, first_resumed = resolve_conversation_checkpoint(
        checkpoint_id,
        agent="orchestrator",
        create=True,
    )
    second_id, second_resumed = resolve_conversation_checkpoint(
        checkpoint_id,
        agent="orchestrator",
        create=True,
    )
    assert first_resumed is False
    assert second_resumed is True
    assert second_id == first_id


def test_low_level_insert_keeps_a_valid_checkpoint_default(checkpoint_db):
    from database import get_db

    with get_db() as connection:
        conversation_id = connection.execute(
            "INSERT INTO conversations (title) VALUES ('outil interne')"
        ).lastrowid
        checkpoint_id = connection.execute(
            "SELECT checkpoint_id FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()[0]

    parsed = uuid.UUID(checkpoint_id)
    assert parsed.version == 4
    assert parsed.variant == uuid.RFC_4122


def test_legacy_migration_backfills_checkpoint_and_title_metadata():
    from database.migrations import _migrate_conversations

    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            ended_at DATETIME,
            agent TEXT,
            summary TEXT
        )
        """
    )
    connection.execute("INSERT INTO conversations (agent) VALUES ('legacy')")
    _migrate_conversations(connection)
    connection.execute(
        "UPDATE conversations SET title = 'Titre historique' WHERE id = 1"
    )
    _migrate_conversations(connection)

    row = connection.execute(
        "SELECT checkpoint_id, title_status, title_source FROM conversations WHERE id = 1"
    ).fetchone()
    assert str(uuid.UUID(row[0])) == row[0]
    assert row[1:] == ("manual", "legacy")


@pytest.mark.asyncio
async def test_ai_title_updates_metadata_and_has_local_fallback(
    checkpoint_db,
    monkeypatch: pytest.MonkeyPatch,
):
    from api import conversation_titles
    from database import create_conversation, get_conversation_detail, save_message

    monkeypatch.setattr(
        conversation_titles, "_schedule_llm_log", lambda **_kwargs: None
    )

    async def generated_title(**_kwargs):
        return {"content": "Architecture conversation durable"}

    monkeypatch.setattr(conversation_titles.llm, "chat", generated_title)
    conversation_id = create_conversation()
    save_message(conversation_id, "user", "Revois les conversations")
    save_message(conversation_id, "assistant", "Je m'en occupe.")

    assert await conversation_titles._maybe_title_conversation(conversation_id) == (
        "Architecture conversation durable"
    )
    detail = get_conversation_detail(conversation_id)
    assert detail["title_status"] == "ready"
    assert detail["title_source"] == "ai"

    async def unavailable_title(**_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(conversation_titles.llm, "chat", unavailable_title)
    fallback_id = create_conversation()
    save_message(fallback_id, "user", "répare la reprise après reconnexion maintenant")
    save_message(fallback_id, "assistant", "Bien reçu.")

    fallback = await conversation_titles._maybe_title_conversation(fallback_id)
    fallback_detail = get_conversation_detail(fallback_id)
    assert fallback == "Répare la reprise après reconnexion maintenant"
    assert fallback_detail["title_status"] == "fallback"
    assert fallback_detail["title_source"] == "first_user_message"


@pytest.mark.asyncio
async def test_websocket_receives_pending_then_final_ai_title(
    checkpoint_db,
    monkeypatch: pytest.MonkeyPatch,
):
    from api import conversation_titles
    from database import create_conversation, save_message

    sent: list[dict] = []

    class FakeWebSocket:
        async def send_json(self, payload: dict) -> None:
            sent.append(payload)

    async def generated_title(**_kwargs):
        return {"content": "Reconnexion conversation fiable"}

    monkeypatch.setattr(conversation_titles.llm, "chat", generated_title)
    monkeypatch.setattr(
        conversation_titles, "_schedule_llm_log", lambda **_kwargs: None
    )
    conversation_id = create_conversation()
    save_message(conversation_id, "user", "Garde cette conversation ouverte")
    save_message(conversation_id, "assistant", "La reprise est activée.")

    await conversation_titles.notify_and_schedule_conversation_title(
        FakeWebSocket(),
        conversation_id,
    )
    await conversation_titles._title_tasks[conversation_id]

    assert sent[0]["type"] == "conversation_updated"
    assert sent[0]["title_status"] == "pending"
    assert sent[-1]["title"] == "Reconnexion conversation fiable"
    assert sent[-1]["title_status"] == "ready"


@pytest.mark.asyncio
async def test_ai_title_never_overwrites_concurrent_manual_rename(
    checkpoint_db,
    monkeypatch: pytest.MonkeyPatch,
):
    from api import conversation_titles
    from database import (
        create_conversation,
        get_conversation_detail,
        save_message,
        update_conversation,
    )

    conversation_id = create_conversation()
    save_message(conversation_id, "user", "Donne un titre à cette discussion")
    save_message(conversation_id, "assistant", "Je prépare le titre.")

    async def delayed_title(**_kwargs):
        update_conversation(
            conversation_id,
            title="Mon titre manuel",
            title_status="manual",
            title_source="user",
            title_updated_at="2026-08-10T20:00:00+00:00",
        )
        return {"content": "Titre proposé par IA"}

    monkeypatch.setattr(conversation_titles.llm, "chat", delayed_title)
    monkeypatch.setattr(conversation_titles, "_schedule_llm_log", lambda **_kwargs: None)

    result = await conversation_titles._maybe_title_conversation(conversation_id)
    detail = get_conversation_detail(conversation_id)
    assert result == "Mon titre manuel"
    assert detail["title"] == "Mon titre manuel"
    assert detail["title_status"] == "manual"

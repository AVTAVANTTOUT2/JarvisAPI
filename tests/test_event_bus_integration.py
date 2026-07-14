"""Intégration des producteurs, consommateurs et du journal Phase 3."""

from __future__ import annotations

import asyncio

import pytest

import database
from jarvis.event_bus import DOMAIN_EVENT_TYPES, event_bus
from websocket_registry import add_websocket, remove_websocket


class _FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, event: dict) -> None:
        self.messages.append(event)


@pytest.mark.asyncio
async def test_database_mutations_emit_log_and_push_all_phase3_events(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "phase3.db")
    monkeypatch.setattr(database, "_dispatch_semantic_indexing", lambda *_args: None)
    monkeypatch.setattr(database, "_dispatch_push_notification", lambda *_args: None)
    database.init_db()
    await event_bus.wait_until_idle()

    from scripts import audio_daemon as audio_module

    spoken: list[tuple[str, str]] = []

    async def capture_tts(_daemon, text: str, emotion: str = "neutral") -> None:
        spoken.append((text, emotion))

    monkeypatch.setattr(audio_module.audio_daemon, "enabled", True)
    monkeypatch.setattr(audio_module.AudioDaemon, "_play_tts", capture_tts)

    with database.get_db() as conn:
        table_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            if not row[0].startswith("sqlite_")
        }
    assert "event_log" in table_names
    assert len(table_names) == 73

    queue = event_bus.subscribe()
    socket = _FakeWebSocket()
    await add_websocket(socket)
    try:
        notification_id = database.create_notification(
            "tests", "Phase 3", "Push temps réel", priority="high"
        )
        task_id = database.create_task("Tester les événements", priority="high")
        assert database.update_task_status(task_id, "done") is True
        conversation_id = database.create_conversation(agent="tests")
        message_id = database.save_message(conversation_id, "user", "Bonjour bus")
        context_id = database.add_life_context("project", "Phase 3 en cours")
        person_id = database.upsert_person("Ada Lovelace", relationship="collègue")
        assert database.upsert_person("Ada Lovelace") == person_id
        episode_id = database.save_episode("tests", "Épisode", summary="Résumé")
        pattern_id = database.create_pattern("behavioral", "Travail concentré")
        fact_id = database.add_fact("project", "Phase 3 active")

        assert all(
            value > 0
            for value in (
                notification_id,
                task_id,
                conversation_id,
                message_id,
                context_id,
                person_id,
                episode_id,
                pattern_id,
                fact_id,
            )
        )
        await event_bus.wait_until_idle()
        assert spoken == [("Phase 3. Push temps réel", "alert")]

        received = []
        while True:
            try:
                received.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        received_types = {event.event_type for event in received}
        assert received_types == set(DOMAIN_EVENT_TYPES)
        assert {message["event_type"] for message in socket.messages} == set(
            DOMAIN_EVENT_TYPES
        )

        rows = database.get_event_log(limit=50)
        domain_rows = [row for row in rows if row["event_type"] in DOMAIN_EVENT_TYPES]
        assert {row["event_type"] for row in domain_rows} == set(DOMAIN_EVENT_TYPES)
        assert all(len(row["checksum"]) == 64 for row in domain_rows)
        assert all(isinstance(row["payload"], dict) for row in domain_rows)

        duplicate = received[0]
        await event_bus.emit(duplicate)
        with database.get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE event_id = ?",
                (duplicate.event_id,),
            ).fetchone()[0]
        assert count == 1
    finally:
        event_bus.unsubscribe(queue)
        await remove_websocket(socket)

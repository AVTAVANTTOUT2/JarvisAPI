"""Tests du registre persistant des offsets iMessage."""

from __future__ import annotations

import asyncio
import sqlite3

import database
import pytest
import integrations.imessage_reader as reader_module
from integrations.imessage_cursor import (
    advance_consumer_cursor,
    get_consumer_cursor,
    initialize_consumer_cursor,
)
from integrations.imessage_reader import IMessageReader


def _create_cursor_table(db_path) -> None:
    database.DB_PATH = db_path
    with database.get_db() as conn:
        conn.execute(
            """CREATE TABLE imessage_consumer_cursors (
                consumer TEXT PRIMARY KEY,
                last_apple_rowid INTEGER NOT NULL DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )"""
        )


def test_consumer_offsets_are_independent_and_monotone(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "jarvis.db")
    _create_cursor_table(database.DB_PATH)

    assert initialize_consumer_cursor("reader", 10) == 10
    assert initialize_consumer_cursor("reader", 99) == 10
    assert initialize_consumer_cursor("bridge", 20) == 20
    assert advance_consumer_cursor("reader", 15) == 15
    assert advance_consumer_cursor("reader", 12) == 15
    assert get_consumer_cursor("bridge") == 20


def test_reader_cursor_survives_reader_restart(tmp_path, monkeypatch):
    jarvis_db = tmp_path / "jarvis.db"
    monkeypatch.setattr(database, "DB_PATH", jarvis_db)
    _create_cursor_table(jarvis_db)

    chat_db = tmp_path / "chat.db"
    with sqlite3.connect(chat_db) as conn:
        conn.execute("CREATE TABLE message (text TEXT)")
        conn.executemany("INSERT INTO message(text) VALUES (?)", [("a",), ("b",)])

    reader = IMessageReader()
    reader.db_path = chat_db
    reader._available = True
    assert reader.scan_new_messages_with_last_id() == (0, 2)

    with sqlite3.connect(chat_db) as conn:
        conn.execute("INSERT INTO message(text) VALUES ('c')")

    assert reader.scan_new_messages_with_last_id() == (1, 3)

    restarted = IMessageReader()
    restarted.db_path = chat_db
    restarted._available = True
    assert restarted.scan_new_messages_with_last_id() == (0, 3)


@pytest.mark.asyncio
async def test_intelligence_uses_apple_rows_not_jarvis_message_ids(monkeypatch):
    messages = [
        {
            "rowid": 42,
            "text": "Message Apple unique",
            "is_from_me": False,
            "handle": "gregoire@example.test",
        }
    ]

    class AppleStub:
        def get_new_messages(self, since_rowid: int, *, limit: int):
            assert since_rowid == 41
            assert limit == 100
            return messages

    captured = {}

    async def fake_analyze(raw_messages, *, since_id, source):
        captured.update(
            raw_messages=raw_messages,
            since_id=since_id,
            source=source,
        )
        return {"status": "ok"}

    import jarvis.message_intelligence as intelligence

    monkeypatch.setattr(reader_module, "apple_data", AppleStub())
    monkeypatch.setattr(intelligence, "analyze_message_batch", fake_analyze)

    ok = await reader_module._trigger_message_intelligence(since_rowid=41)

    assert ok is True
    assert captured == {
        "raw_messages": messages,
        "since_id": 41,
        "source": "imessage",
    }


@pytest.mark.asyncio
async def test_periodic_path_advances_cursor_only_after_intelligence_ok(
    tmp_path, monkeypatch
):
    jarvis_db = tmp_path / "jarvis.db"
    monkeypatch.setattr(database, "DB_PATH", jarvis_db)
    _create_cursor_table(jarvis_db)

    messages = [
        {
            "rowid": 11,
            "text": "Salut",
            "is_from_me": False,
            "handle": "+33600000000",
        }
    ]
    initialize_consumer_cursor("reader.intelligence", 10)

    class AppleStub:
        def is_available(self) -> bool:
            return True

        def get_max_rowid(self) -> int:
            return 11

        def get_new_messages(self, since_rowid: int, limit: int = 100, **_kwargs):
            assert since_rowid == 10
            return messages

    async def fail_analyze(*_a, **_k):
        return {"status": "error"}

    import jarvis.message_intelligence as intelligence

    reader = IMessageReader()
    reader.cursor_name = "reader.intelligence"
    reader._apple_data = AppleStub()
    reader._available = True
    monkeypatch.setattr(intelligence, "analyze_message_batch", fail_analyze)
    monkeypatch.setattr(reader, "sync_knowledge_mirror", lambda: {"status": "ok"})

    peeked, since = reader.peek_new_messages(limit=10)
    assert since == 10
    assert len(peeked) == 1
    ok = await reader_module._trigger_message_intelligence(
        since_rowid=since, messages=peeked
    )
    assert ok is False
    assert get_consumer_cursor("reader.intelligence") == 10

    async def ok_analyze(*_a, **_k):
        return {"status": "ok"}

    monkeypatch.setattr(intelligence, "analyze_message_batch", ok_analyze)
    ok = await reader_module._trigger_message_intelligence(
        since_rowid=since, messages=peeked
    )
    assert ok is True
    advance_consumer_cursor("reader.intelligence", 11)
    assert get_consumer_cursor("reader.intelligence") == 11


@pytest.mark.asyncio
async def test_daemon_still_scans_when_bridge_running_for_other_contacts(
    monkeypatch,
):
    from scripts.jarvis_daemon import JarvisDaemon

    class Bridge:
        running = True
        target = "+33611111111"

    class AppleStub:
        def get_new_messages(self, since_rowid, limit=50, incoming_only=False):
            return [
                {
                    "rowid": since_rowid + 1,
                    "text": "Coucou",
                    "is_from_me": False,
                    "handle": "+33622222222",
                },
                {
                    "rowid": since_rowid + 2,
                    "text": "Self target",
                    "is_from_me": False,
                    "handle": "+33611111111",
                },
            ]

    created = []

    class Notif:
        @staticmethod
        def create(**kwargs):
            created.append(kwargs)

    monkeypatch.setattr("integrations.imessage.imessage_bridge", Bridge())
    monkeypatch.setattr(
        "integrations.imessage_reader.imessage_reader",
        type("R", (), {"is_available": staticmethod(lambda: True)})(),
    )
    monkeypatch.setattr("scripts.jarvis_daemon.apple_data", AppleStub())
    monkeypatch.setattr("scripts.jarvis_daemon.notification_service", Notif)
    monkeypatch.setattr(
        "integrations.imessage_cursor.get_consumer_cursor", lambda _n: 100
    )
    advanced = []
    monkeypatch.setattr(
        "integrations.imessage_cursor.advance_consumer_cursor",
        lambda n, v: advanced.append((n, v)) or v,
    )

    daemon = JarvisDaemon.__new__(JarvisDaemon)
    daemon.imessage_cursor_name = "daemon.notifications"
    daemon.known_msg_ids = set()
    daemon.tts_queue = asyncio.Queue()

    async def triage(_text):
        return False

    daemon._local_triage = triage
    await daemon._check_imessage()

    assert advanced == [("daemon.notifications", 102)]
    assert len(created) == 1
    assert created[0]["title"] == "Message de +33622222222"

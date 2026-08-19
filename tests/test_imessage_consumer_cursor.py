"""Tests du registre persistant des offsets iMessage."""

from __future__ import annotations

import asyncio

import database
import pytest
import integrations.imessage_reader as reader_module
from integrations.imessage_cursor import (
    advance_consumer_cursor,
    get_consumer_cursor,
    initialize_consumer_cursor,
)
from integrations.imessage_reader import IMessageReader


def _init_mirror(db_path, monkeypatch) -> None:
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    from database import init_db

    init_db()


def _insert_message(
    *,
    apple_rowid: int,
    text: str,
    handle: str = "+33600000000",
    is_from_me: int = 0,
    display_name: str = "",
    guid: str | None = None,
) -> None:
    from database import get_db

    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM imessage_handles WHERE handle = ?", (handle,)
        ).fetchone()
        if row:
            handle_id = int(row["id"])
        else:
            conn.execute(
                """INSERT INTO imessage_handles
                   (apple_handle_id, handle, display_name)
                   VALUES (?, ?, ?)""",
                (apple_rowid, handle, display_name),
            )
            handle_id = int(
                conn.execute(
                    "SELECT id FROM imessage_handles WHERE handle = ?",
                    (handle,),
                ).fetchone()["id"]
            )
        conn.execute(
            """INSERT INTO imessage_messages
               (apple_rowid, guid, handle_id, text, date, is_from_me)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                apple_rowid,
                guid or f"guid-{apple_rowid}",
                handle_id,
                text,
                700_000_000 + apple_rowid,
                is_from_me,
            ),
        )


def test_consumer_offsets_are_independent_and_monotone(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "jarvis.db")
    from database import get_db, init_db

    init_db()
    with get_db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS imessage_consumer_cursors (
                consumer TEXT PRIMARY KEY,
                last_apple_rowid INTEGER NOT NULL DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )"""
        )

    assert initialize_consumer_cursor("reader", 10) == 10
    assert initialize_consumer_cursor("reader", 99) == 10
    assert initialize_consumer_cursor("bridge", 20) == 20
    assert advance_consumer_cursor("reader", 15) == 15
    assert advance_consumer_cursor("reader", 12) == 15
    assert get_consumer_cursor("bridge") == 20


def test_reader_cursor_survives_reader_restart(tmp_path, monkeypatch):
    jarvis_db = tmp_path / "jarvis.db"
    _init_mirror(jarvis_db, monkeypatch)
    _insert_message(apple_rowid=1, text="a")
    _insert_message(apple_rowid=2, text="b")

    reader = IMessageReader()
    assert reader.scan_new_messages_with_last_id() == (0, 2)

    _insert_message(apple_rowid=3, text="c")

    assert reader.scan_new_messages_with_last_id() == (1, 3)

    restarted = IMessageReader()
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

    captured = {}

    async def fake_analyze(raw_messages, *, since_id, source):
        captured.update(
            raw_messages=raw_messages,
            since_id=since_id,
            source=source,
        )
        return {"status": "ok"}

    import jarvis.message_intelligence as intelligence

    monkeypatch.setattr(
        reader_module.imessage_reader,
        "get_new_messages",
        lambda since_rowid, limit=100, **_k: messages,
    )
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
    from database import init_db

    init_db()
    initialize_consumer_cursor("reader.intelligence", 10)

    messages = [
        {
            "rowid": 11,
            "text": "Salut",
            "is_from_me": False,
            "handle": "+33600000000",
        }
    ]

    async def fail_analyze(*_a, **_k):
        return {"status": "error"}

    import jarvis.message_intelligence as intelligence

    reader = IMessageReader()
    reader.cursor_name = "reader.intelligence"
    reader._available = True
    reader.get_max_rowid = lambda: 11  # type: ignore[method-assign]
    reader.get_new_messages = lambda since_rowid, **_k: messages  # type: ignore[method-assign]
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

    class ReaderStub:
        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def get_new_messages(since_rowid, limit=50, incoming_only=False, **_k):
            del incoming_only, limit
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
        ReaderStub(),
    )
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

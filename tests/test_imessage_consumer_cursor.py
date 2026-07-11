"""Tests du registre persistant des offsets iMessage."""

from __future__ import annotations

import sqlite3

import database
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

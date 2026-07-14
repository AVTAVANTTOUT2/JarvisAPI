"""Contrats de l'AppleDataService introduit en Phase 5."""

from __future__ import annotations

import ast
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from integrations.apple_data import (
    AppleDataService,
    apple_epoch_to_datetime,
    datetime_to_apple_epoch,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APPLE_DATA_MODULE = PROJECT_ROOT / "integrations" / "apple_data.py"
CHAT_DB_CONSUMERS = (
    "api/lifespan.py",
    "integrations/imessage.py",
    "integrations/imessage_import.py",
    "integrations/imessage_reader.py",
    "scripts/backfill_imessages.py",
    "scripts/imessage_daemon.py",
    "scripts/imessage_import.py",
    "scripts/imessage_sync_health_check.py",
    "scripts/jarvis_daemon.py",
    "scripts/test_macos_permissions.py",
    "tv/data_sources/messages.py",
)


@pytest.fixture
def chat_db(tmp_path):
    path = tmp_path / "chat.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
            CREATE TABLE message (
                ROWID INTEGER PRIMARY KEY,
                text TEXT,
                date INTEGER,
                is_from_me INTEGER,
                handle_id INTEGER
            );
            CREATE TABLE chat (ROWID INTEGER PRIMARY KEY);
            CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);

            INSERT INTO handle (ROWID, id) VALUES
                (1, '+33600000001'),
                (2, 'friend@example.com');
            INSERT INTO chat (ROWID) VALUES (1), (2);
            INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (1, 1), (2, 2);
            INSERT INTO message (ROWID, text, date, is_from_me, handle_id) VALUES
                (1, 'Bonjour', 700000000, 0, 1),
                (2, 'Réponse', 700000001000000000, 1, 1),
                (3, 'Autre contact', 700000002, 0, 2),
                (4, NULL, 700000003, 0, 1);
            """
        )
    return path


def test_apple_epoch_conversion_is_central_and_bidirectional():
    seconds = apple_epoch_to_datetime(700_000_000)
    nanoseconds = apple_epoch_to_datetime(700_000_000_000_000_000)

    assert seconds == nanoseconds
    assert seconds == datetime(2023, 3, 8, 20, 26, 40)
    assert apple_epoch_to_datetime(seconds.isoformat()) == seconds
    assert apple_epoch_to_datetime(0) is None
    assert apple_epoch_to_datetime(0, zero_is_none=False) == datetime(2001, 1, 1)
    assert datetime_to_apple_epoch(seconds, nanoseconds=False) == 700_000_000
    assert datetime_to_apple_epoch(
        seconds.replace(tzinfo=timezone.utc),
        nanoseconds=True,
    ) == 700_000_000_000_000_000


def test_backfill_compatibility_wrapper_preserves_utc_iso_format():
    from scripts.backfill_imessages import apple_ts_to_datetime

    converted = apple_ts_to_datetime(700_000_000)

    assert converted is not None
    assert converted.tzinfo == timezone.utc
    assert converted.isoformat().endswith("+00:00")


def test_connection_is_read_only_and_uses_injected_path(chat_db):
    service = AppleDataService(chat_db)

    assert service.is_available() is True
    assert service.count_messages() == 4
    assert service.get_max_rowid() == 4
    connection = service.connect_readonly()
    with connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM message")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_get_new_messages_filters_target_and_direction(chat_db):
    service = AppleDataService(chat_db)

    messages = service.get_new_messages(
        0,
        handle="+33600000001",
        incoming_only=True,
    )

    assert messages == [
        {
            "rowid": 1,
            "text": "Bonjour",
            "date": 700_000_000,
            "is_from_me": False,
            "handle": "+33600000001",
        }
    ]


def test_conversation_search_and_stats_keep_historical_shapes(chat_db):
    service = AppleDataService(chat_db)

    conversation = service.get_conversation("+33600000001", limit=10)
    search = service.search_messages("contact")
    stats = service.get_all_conversation_stats()

    assert [message["rowid"] for message in conversation] == [1, 2]
    assert conversation[0]["date_short"] == "08/03 20:26"
    assert search[0]["handle"] == "friend@example.com"
    assert stats[0]["handle"] == "+33600000001"
    assert stats[0]["msg_count"] == 2
    assert stats[0]["first_unix_ts"] == pytest.approx(1_678_307_200.0)


def test_resolve_handle_uses_the_injected_contacts_provider(chat_db):
    class FakeContacts:
        def resolve_handle(self, handle: str) -> str:
            return {"+33600000001": "Alice"}.get(handle, handle)

    service = AppleDataService(chat_db, contacts=FakeContacts())

    assert service.resolve_handle("+33600000001") == "Alice"
    assert service.resolve_handle("unknown") == "unknown"


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
        return f"{call.func.value.id}.{call.func.attr}"
    return None


def test_chat_db_opening_and_timestamp_conversion_stay_centralized():
    """Empêche le retour des lecteurs SQLite directs de Messages.app."""
    conversion_definitions: list[Path] = []

    for path in PROJECT_ROOT.rglob("*.py"):
        if "tests" in path.parts or ".git" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "apple_epoch_to_datetime":
                    conversion_definitions.append(path)
            if isinstance(node, ast.BinOp) and path != APPLE_DATA_MODULE:
                expression = ast.get_source_segment(source, node) or ""
                assert not (
                    "Messages" in expression and "chat.db" in expression
                ), (
                    f"{path.relative_to(PROJECT_ROOT)} reconstruit le chemin de chat.db; "
                    "utiliser AppleDataService"
                )
            if not isinstance(node, ast.Call) or _call_name(node) != "sqlite3.connect":
                continue
            argument_source = ast.get_source_segment(source, node.args[0]) if node.args else ""
            assert "chat" not in (argument_source or "").lower(), (
                f"{path.relative_to(PROJECT_ROOT)} ouvre directement chat.db; "
                "utiliser AppleDataService"
            )

    assert conversion_definitions == [APPLE_DATA_MODULE]

    for relative_path in CHAT_DB_CONSUMERS:
        source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        assert "apple_data" in source, f"{relative_path} doit dépendre d'AppleDataService"
        tree = ast.parse(source, filename=relative_path)
        direct_chat_openers = [
            ast.get_source_segment(source, node.args[0]) or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and _call_name(node) == "sqlite3.connect"
            and node.args
            and any(
                marker in (ast.get_source_segment(source, node.args[0]) or "").lower()
                for marker in ("chat", "imessage")
            )
        ]
        assert not direct_chat_openers, (
            f"{relative_path} ne doit pas ouvrir directement chat.db"
        )

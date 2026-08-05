"""Contrats de purge complète et atomique des conversations."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture()
def conversation_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path]:
    db_path = tmp_path / "conversation-purge.db"
    upload_root = tmp_path / "uploads"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.UPLOAD_DIR", str(upload_root))

    from database import init_db

    init_db()
    return db_path, upload_root


def _seed_conversation_graph(upload_root: Path) -> tuple[int, int, Path]:
    from database import get_db

    stored_path = upload_root / "conversations" / "1" / "private.txt"
    stored_path.parent.mkdir(parents=True)
    stored_path.write_text("secret", encoding="utf-8")

    with get_db() as conn:
        target_id = int(
            conn.execute(
                "INSERT INTO conversations (title) VALUES ('target')"
            ).lastrowid
        )
        keep_id = int(
            conn.execute("INSERT INTO conversations (title) VALUES ('keep')").lastrowid
        )
        for conversation_id, content in ((target_id, "secret"), (keep_id, "keep")):
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', ?)",
                (conversation_id, content),
            )
            conn.execute(
                """
                INSERT INTO agentic_workflows
                    (conversation_id, user_message, steps_json)
                VALUES (?, ?, '[]')
                """,
                (conversation_id, content),
            )
            recording_id = int(
                conn.execute(
                    "INSERT INTO recordings (conversation_id, transcription) VALUES (?, ?)",
                    (conversation_id, content),
                ).lastrowid
            )
            conn.execute(
                """
                INSERT INTO conversation_turns
                    (recording_id, turn_order, speaker_label, text)
                VALUES (?, 0, 'A', ?)
                """,
                (recording_id, content),
            )
            conn.execute(
                """
                INSERT INTO mobile_chat_dedup
                    (device_id, client_message_id, conversation_id, response_json)
                VALUES ('phone', ?, ?, ?)
                """,
                (
                    f"message-{conversation_id}",
                    conversation_id,
                    json.dumps({"text": content}),
                ),
            )
            conn.execute(
                """
                INSERT INTO event_log
                    (event_id, event_type, timestamp, source, payload_json, checksum)
                VALUES (?, 'message.sent', 1.0, 'test', ?, 'checksum')
                """,
                (
                    f"event-{conversation_id}",
                    json.dumps(
                        {"conversation_id": conversation_id, "content_preview": content}
                    ),
                ),
            )

        conn.execute(
            """
            INSERT INTO conversation_documents
                (conversation_id, filename, original_name, file_path)
            VALUES (?, 'private.txt', 'private.txt', ?)
            """,
            (target_id, str(stored_path)),
        )
        keep_path = upload_root / "conversations" / str(keep_id) / "keep.txt"
        keep_path.parent.mkdir(parents=True)
        keep_path.write_text("keep", encoding="utf-8")
        conn.execute(
            """
            INSERT INTO conversation_documents
                (conversation_id, filename, original_name, file_path)
            VALUES (?, 'keep.txt', 'keep.txt', ?)
            """,
            (keep_id, str(keep_path)),
        )
        conn.execute(
            "INSERT INTO school_documents (title, file_path) VALUES ('shared', ?)",
            (str(stored_path),),
        )

    return target_id, keep_id, stored_path


def test_delete_conversation_removes_every_linked_record(
    conversation_db: tuple[Path, Path],
) -> None:
    from database import delete_conversation, get_db
    from database.event_log import _persist_event
    from jarvis.events import MessageSent

    _, upload_root = conversation_db
    target_id, keep_id, stored_path = _seed_conversation_graph(upload_root)

    assert delete_conversation(target_id) is True

    with get_db() as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (target_id,)
            ).fetchone()
            is None
        )
        for table in (
            "messages",
            "agentic_workflows",
            "recordings",
            "conversation_documents",
            "mobile_chat_dedup",
        ):
            assert (
                conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE conversation_id = ?",
                    (target_id,),
                ).fetchone()[0]
                == 0
            )
            assert (
                conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE conversation_id = ?",
                    (keep_id,),
                ).fetchone()[0]
                == 1
            )

        assert (
            conn.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0] == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM school_documents WHERE file_path = ?",
                (str(stored_path),),
            ).fetchone()[0]
            == 0
        )
        event_payloads = [
            json.loads(row["payload_json"])
            for row in conn.execute("SELECT payload_json FROM event_log").fetchall()
        ]
        assert {payload["conversation_id"] for payload in event_payloads} == {keep_id}

    assert not stored_path.exists()
    assert delete_conversation(target_id) is False

    # Un événement encore en file au moment du DELETE ne doit pas recréer une
    # trace contenant un aperçu du message après la purge.
    _persist_event(MessageSent(target_id, 999, "user", "secret tardif"))
    with get_db() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE payload_json LIKE '%secret tardif%'"
            ).fetchone()[0]
            == 0
        )


def test_delete_conversation_rolls_back_the_whole_graph_on_failure(
    conversation_db: tuple[Path, Path],
) -> None:
    from database import delete_conversation, get_db

    _, upload_root = conversation_db
    target_id, _, stored_path = _seed_conversation_graph(upload_root)
    with get_db() as conn:
        conn.execute(f"""
            CREATE TRIGGER abort_target_conversation_delete
            BEFORE DELETE ON conversations
            WHEN OLD.id = {target_id}
            BEGIN
                SELECT RAISE(ABORT, 'test rollback');
            END;
            """)

    with pytest.raises(sqlite3.IntegrityError, match="test rollback"):
        delete_conversation(target_id)

    with get_db() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE id = ?", (target_id,)
            ).fetchone()[0]
            == 1
        )
        for table in (
            "messages",
            "agentic_workflows",
            "recordings",
            "conversation_documents",
            "mobile_chat_dedup",
        ):
            assert (
                conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE conversation_id = ?",
                    (target_id,),
                ).fetchone()[0]
                == 1
            )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE payload_json LIKE ?",
                (f'%"conversation_id": {target_id}%',),
            ).fetchone()[0]
            == 1
        )

    assert stored_path.exists()

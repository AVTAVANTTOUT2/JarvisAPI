"""Contrats de compatibilité des modules extraits de `database.__init__`."""

from __future__ import annotations

import database
from database import conversation_turns, embeddings, push, sessions, settings, tasks


def test_phase2_reexports_keep_historical_api():
    assert database.get_connection is database.core.get_connection
    assert database.get_db is database.core.get_db
    assert database.get_setting is settings.get_setting
    assert database.set_setting is settings.set_setting
    assert database.get_tasks is tasks.get_tasks
    assert database.create_task is tasks.create_task
    assert database.update_task_status is tasks.update_task_status
    assert database.create_session_row is sessions.create_session_row
    assert database.upsert_push_subscription is push.upsert_push_subscription
    assert database.save_conversation_turns is conversation_turns.save_conversation_turns
    assert database.upsert_memory_embedding is embeddings.upsert_memory_embedding


def test_extracted_modules_follow_dynamic_database_path(tmp_path, monkeypatch):
    db_path = tmp_path / "phase2.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()

    database.set_setting("phase2", "active")
    task_id = database.create_task("Extraire database", priority="high")

    assert database.get_setting("phase2") == "active"
    assert database.get_task(task_id)["title"] == "Extraire database"
    assert database.update_task_status(task_id, "done") is True
    assert database.get_task(task_id)["status"] == "done"
    assert db_path.exists()

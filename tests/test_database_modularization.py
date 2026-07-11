"""Contrats de compatibilité des modules extraits de `database.__init__`."""

from __future__ import annotations

import database
from database import (
    conversation_turns,
    email,
    embeddings,
    facts,
    push,
    relationships,
    sessions,
    settings,
    stats,
    tasks,
)


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
    assert database.upsert_email_summary is email.upsert_email_summary
    assert database.add_fact is facts.add_fact
    assert database.upsert_relationship_profile is relationships.upsert_relationship_profile
    assert database.get_cost_summary is stats.get_cost_summary


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


def test_second_phase2_domains_preserve_crud_behavior(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "domains.db")
    database.init_db()

    email_id = database.upsert_email_summary(
        "mail-1", "alice@example.test", "Sujet", "Résumé", action_needed=True
    )
    assert email_id > 0
    assert database.get_email_stats() == {"total": 1, "unread": 1, "urgent": 0}

    fact_id = database.add_fact("preference", "Préfère le local-first")
    assert database.search_facts("local-first")[0]["id"] == fact_id

    person_id = database.upsert_person("Alice")
    profile_id = database.upsert_relationship_profile(
        person_id, communication_style="direct"
    )
    assert profile_id > 0
    assert database.get_relationship_profile(person_id)["communication_style"] == "direct"

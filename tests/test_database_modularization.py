"""Contrats de compatibilité des modules extraits de `database.__init__`."""

from __future__ import annotations

import inspect
from pathlib import Path

import database
from database import (
    conversations,
    conversation_turns,
    core,
    devagent,
    devops,
    email,
    embeddings,
    episodes,
    event_log,
    facts,
    location_helpers,
    migrations,
    notifications,
    patterns,
    people,
    push,
    relationships,
    rituals,
    school,
    sessions,
    settings,
    screen_daemon,
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
    assert database.save_screen_activity is screen_daemon.save_screen_activity
    assert database.register_device is screen_daemon.register_device
    assert database.save_message is conversations.save_message
    assert database.save_episode is episodes.save_episode
    assert database.get_person is people.get_person
    assert database.save_mood is patterns.save_mood
    assert database.create_notification is notifications.create_notification
    assert database.set_daily_ritual is rituals.set_daily_ritual
    assert database.get_perf_history is devops.get_perf_history
    assert database.save_school_document is school.save_school_document


def test_database_package_is_a_small_typed_compatibility_facade():
    facade = Path(database.__file__)
    assert len(facade.read_text(encoding="utf-8").splitlines()) < 500

    modules = (
        conversation_turns,
        conversations,
        core,
        devagent,
        devops,
        email,
        embeddings,
        episodes,
        event_log,
        facts,
        location_helpers,
        migrations,
        notifications,
        patterns,
        people,
        push,
        relationships,
        rituals,
        school,
        screen_daemon,
        sessions,
        settings,
        stats,
        tasks,
    )
    for module in modules:
        assert inspect.getdoc(module), module.__name__
        public_functions = [
            function
            for name, function in inspect.getmembers(module, inspect.isfunction)
            if not name.startswith("_") and function.__module__ == module.__name__
        ]
        assert public_functions, module.__name__
        for function in public_functions:
            signature = inspect.signature(function)
            assert signature.return_annotation is not inspect.Signature.empty, function
            for parameter in signature.parameters.values():
                assert parameter.annotation is not inspect.Signature.empty, function


def test_database_modules_do_not_import_the_compatibility_facade():
    package_dir = Path(database.__file__).parent
    offenders = []
    for path in package_dir.glob("*.py"):
        if path.name == "__init__.py":
            continue
        if "from database import" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert offenders == []


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


def test_screen_daemon_domain_preserves_crud_behavior(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "screen.db")
    database.init_db()

    token = database.register_device("mac-mini", "Mac mini")
    assert token
    database.set_active_device("mac-mini")
    assert database.get_active_device()["device_id"] == "mac-mini"

    database.upsert_app_usage("mac-mini", "Codex", 30)
    database.upsert_app_usage("mac-mini", "Codex", 15)
    usage = database.get_app_usage(device="mac-mini")
    assert usage[0]["duration_seconds"] == 45
    assert usage[0]["session_count"] == 2

    activity_id = database.save_screen_activity(
        "mac-mini", "Codex", "Refactoring", mood="focused"
    )
    assert activity_id > 0
    assert database.get_current_screen_context("mac-mini")["activity"] == "Refactoring"

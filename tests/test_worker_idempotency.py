"""Régressions P11 : concurrence, retry et rerun des workers planifiés."""

from __future__ import annotations

import asyncio
import builtins
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def worker_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "workers.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.EMAIL_WATCHER_LOCK_PATH", str(tmp_path / "email.lock"))
    monkeypatch.setattr("config.RITUALS_TTS", False)
    from database import init_db

    init_db()
    return db_path


def test_job_claim_is_atomic_and_failed_claim_can_expire(worker_db: Path) -> None:
    from database import claim_job_run, release_job_run

    def attempt(_index: int):
        return claim_job_run("concurrent-test", "2026-07-31", now=100.0)

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(attempt, range(8)))

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    assert claim_job_run("concurrent-test", "2026-07-31", now=105.0, lease_seconds=10) is None

    retry = claim_job_run("concurrent-test", "2026-07-31", now=111.0, lease_seconds=10)
    assert retry is not None
    assert release_job_run(retry) is True


class _FakeMailClient:
    def __init__(self) -> None:
        self.email_id = "mail-1"
        self.reset_calls = 0

    def reset_availability_cache(self) -> None:
        self.reset_calls += 1

    def is_available(self) -> bool:
        return True

    async def get_unread(self, _limit: int) -> list[dict]:
        return [{"id": self.email_id, "subject": "Sujet"}]

    async def get_message(self, email_id: str) -> dict:
        assert email_id == self.email_id
        return {
            "id": email_id,
            "from": "Alice <alice@example.test>",
            "subject": "Sujet",
            "body": "Corps du message",
            "date": "2026-07-31 10:00:00",
        }


def _ignored_email_result() -> dict:
    return {
        "content": '{"notify": false, "reason": "ignore", "summary": "Résumé"}'
    }


def _notified_email_result() -> dict:
    return {
        "content": (
            '{"notify": true, "reason": "request", "summary": "Réponse attendue", '
            '"action_needed": "Répondre à Alice"}'
        )
    }


@pytest.mark.asyncio
async def test_email_watcher_and_catchup_share_one_interprocess_cycle(
    worker_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import integrations
    from database import get_all_processed_email_ids
    from scripts import email_watcher as email_module

    mail_client = _FakeMailClient()
    monkeypatch.setattr(integrations, "mail_client", mail_client)

    calls = 0
    notify = MagicMock()
    create_task = MagicMock(return_value=42)
    monkeypatch.setattr("config.DESKTOP_NOTIFICATIONS", False)
    monkeypatch.setattr(email_module.notification_service, "create", notify)
    monkeypatch.setattr(email_module, "create_task", create_task)

    async def slow_chat(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.1)
        return _notified_email_result()

    monkeypatch.setattr(email_module.llm, "chat", slow_chat)
    watcher = email_module.EmailWatcher()
    catchup = email_module.EmailWatcher()
    watcher._send_imessage_alert = AsyncMock()
    catchup._send_imessage_alert = AsyncMock()

    first, second = await asyncio.gather(
        watcher.run_catchup_cycle(),
        catchup.run_catchup_cycle(),
    )

    assert calls == 1
    assert notify.call_count == 1
    assert create_task.call_count == 1
    assert watcher._send_imessage_alert.await_count + catchup._send_imessage_alert.await_count == 1
    assert get_all_processed_email_ids() == {mail_client.email_id}
    assert first["first_cycle_to_analyze"] + second["first_cycle_to_analyze"] == 1


@pytest.mark.asyncio
async def test_email_failure_is_not_marked_processed_and_retries(
    worker_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import integrations
    from database import get_all_processed_email_ids
    from scripts import email_watcher as email_module

    mail_client = _FakeMailClient()
    monkeypatch.setattr(integrations, "mail_client", mail_client)
    chat = AsyncMock(side_effect=[RuntimeError("LLM down"), _ignored_email_result()])
    monkeypatch.setattr(email_module.llm, "chat", chat)
    watcher = email_module.EmailWatcher()
    watcher._initialized = True

    await watcher._check_new_emails()
    assert mail_client.email_id not in watcher.last_processed_ids
    assert get_all_processed_email_ids() == set()
    assert watcher._last_cycle_stats["analysis_failed"] == 1

    await watcher._check_new_emails()
    assert mail_client.email_id in watcher.last_processed_ids
    assert get_all_processed_email_ids() == {mail_client.email_id}
    assert chat.await_count == 2


@pytest.mark.asyncio
async def test_relationship_cursor_retries_failed_batch_without_skipping(
    worker_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from database import get_analysis_cursor, get_total_messages_analyzed
    from integrations.contacts import contacts_reader
    from scripts import relationship_analyzer as relationship_module

    monkeypatch.setattr(contacts_reader, "is_available", lambda: False)
    chat = AsyncMock(
        side_effect=[
            {"content": "pas du json"},
            {"content": '{"person": {"likely_name": "Alice"}}', "cost": 0},
            {"content": '{"person": {"likely_name": "Alice"}}', "cost": 0},
        ]
    )
    monkeypatch.setattr(relationship_module.llm, "chat", chat)
    analyzer = relationship_module.RelationshipAnalyzer()
    analyzer._prompt_template = "{{user_name}} {{handle}} {{messages}}"
    messages = [
        {
            "rowid": rowid,
            "text": f"message {rowid}",
            "is_from_me": rowid % 2,
            "date_short": "31/07 10:00",
        }
        for rowid in range(1, 61)
    ]

    assert await analyzer._process_in_batches("+331234", messages) == 0
    assert get_analysis_cursor("+331234") == 0

    assert await analyzer._process_in_batches("+331234", messages) == 2
    assert get_analysis_cursor("+331234") == 60
    assert get_total_messages_analyzed("+331234") == 60
    assert chat.await_count == 3


@pytest.mark.asyncio
async def test_location_same_window_runs_llm_once(
    worker_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config
    from database import get_db
    from scripts import location_analyzer as location_module

    monkeypatch.setattr(config, "LOCATION_TRACKING", True)
    monkeypatch.setattr(location_module, "get_all_places", lambda: [{"id": 1, "name": "Bureau"}])
    monkeypatch.setattr(
        location_module,
        "visits_summary_last_days",
        lambda _days: [{"id": 4, "place_name": "Bureau", "arrived_at": "2026-07-31 09:00"}],
    )
    monkeypatch.setattr(location_module, "get_today_visits", lambda: [])
    monkeypatch.setattr(location_module, "get_active_location_patterns", lambda: [])
    chat = AsyncMock(return_value={"content": (
        '{"routines_detected": [{"day": "vendredi", "pattern": "Bureau"}], '
        '"suggestions": [], "anomalies": []}'
    )})
    monkeypatch.setattr(location_module.llm, "chat", chat)
    analyzer = location_module.LocationAnalyzer()

    first = await analyzer.run_daily_analysis()
    second = await analyzer.run_daily_analysis()

    assert first["status"] == "completed"
    assert second["status"] == "already_analyzed"
    assert chat.await_count == 1
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM location_patterns").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_roast_concurrent_reruns_do_not_duplicate_llm_or_notification(
    worker_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from database import get_db
    from scripts import rituals

    with get_db() as conn:
        conn.execute(
            "INSERT INTO tasks (title, status, due_date) VALUES ('Dossier', 'todo', '2020-01-01')"
        )

    async def slow_chat(*_args, **_kwargs):
        await asyncio.sleep(0.05)
        return {"content": "Le dossier attend toujours, Monsieur."}

    chat = AsyncMock(side_effect=slow_chat)
    notify = MagicMock()
    monkeypatch.setattr(rituals.llm, "chat", chat)
    monkeypatch.setattr(rituals.notification_service, "create", notify)
    monkeypatch.setattr(rituals, "_speak", MagicMock())

    first, second = await asyncio.gather(rituals.daily_roast(), rituals.daily_roast())
    third = await rituals.daily_roast()

    assert first["roast"] == second["roast"] == third["roast"]
    assert chat.await_count == 1
    assert notify.call_count == 1
    assert second["cached"] is True and third["cached"] is True


@pytest.mark.asyncio
async def test_debrief_and_journal_are_idempotent_by_day(
    worker_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import jarvis_journal, rituals

    debrief_chat = AsyncMock(return_value={"content": "Journée close, Monsieur."})
    journal_chat = AsyncMock(return_value={"content": "J'ai consigné la journée."})
    notify = MagicMock()
    monkeypatch.setattr(rituals.llm, "chat", debrief_chat)
    monkeypatch.setattr(rituals.notification_service, "create", notify)

    first_debrief = await rituals.evening_debrief()
    second_debrief = await rituals.evening_debrief()
    monkeypatch.setattr(jarvis_journal.llm, "chat", journal_chat)
    first_journal = await jarvis_journal.generate_journal_entry("2026-07-31")
    second_journal = await jarvis_journal.generate_journal_entry("2026-07-31")

    assert first_debrief["debrief"] == second_debrief["debrief"]
    assert debrief_chat.await_count == 1
    assert notify.call_count == 1
    assert first_journal["entry"] == second_journal["entry"]
    assert journal_chat.await_count == 1
    assert second_journal["cached"] is True


@pytest.mark.asyncio
async def test_commitment_extraction_same_daily_window_calls_llm_once(
    worker_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import commitments

    monkeypatch.setattr(
        commitments,
        "_todays_user_messages",
        lambda: ["Je promets d'envoyer le dossier demain."],
    )
    chat = AsyncMock(return_value={"content": "[]"})
    monkeypatch.setattr(commitments.llm, "chat", chat)

    assert await commitments.extract_today_commitments() == []
    assert await commitments.extract_today_commitments() == []
    assert chat.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("job_name", "flag_name"),
    [
        ("_run_location_analysis_job", "LOCATION_ANALYSIS_ENABLED"),
        ("scheduled_morning_briefing", "MORNING_BRIEFING_ENABLED"),
        ("check_overdue_tasks", "OVERDUE_TASKS_ENABLED"),
        ("_fitness_reminders_job", "FITNESS_REMINDERS_ENABLED"),
        ("_relationship_alerts_job", "RELATIONSHIP_ALERTS_ENABLED"),
        ("scheduled_evening_summary", "EVENING_SUMMARY_ENABLED"),
        ("scheduled_weekly_summary", "WEEKLY_SUMMARY_ENABLED"),
        ("_relationship_analysis_daily_job", "RELATIONSHIP_ANALYSIS_ENABLED"),
        ("_db_backup_job", "BACKUP_ENABLED"),
        ("_db_maintenance_job", "DB_MAINTENANCE_ENABLED"),
        ("_llm_budget_job", "LLM_BUDGET_CHECK_ENABLED"),
        ("_roast_job", "RITUALS_ENABLED"),
        ("_debrief_job", "RITUALS_ENABLED"),
        ("_quote_job", "RITUALS_ENABLED"),
        ("_birthday_job", "RITUALS_ENABLED"),
        ("_coffee_break_job", "BREAK_ALERTS_ENABLED"),
        ("_weekly_debrief_job", "RITUALS_ENABLED"),
        ("_mood_signal_job", "MOOD_SIGNALS_ENABLED"),
        ("_jarvis_journal_job", "JARVIS_JOURNAL_ENABLED"),
        ("_doomscroll_check_job", "DOOMSCROLL_ALERTS_ENABLED"),
        ("_missed_opportunities_job", "MISSED_OPPORTUNITIES_ENABLED"),
        ("_self_improvement_job", "SELF_IMPROVEMENT_ENABLED"),
        ("_presence_tick_job", "PRESENCE_ENABLED"),
        ("_binge_job", "BINGE_ALERTS_ENABLED"),
        ("_late_return_job", "LATE_RETURN_ENABLED"),
        ("_meeting_tick_job", "MEETING_CAPTURE_ENABLED"),
        ("_commitments_extract_job", "RITUALS_ENABLED"),
        ("_commitments_overdue_job", "RITUALS_ENABLED"),
        ("_duplicate_scan_job", "DUPLICATE_SCAN_ENABLED"),
        ("_security_audit_job", "SECURITY_AUDIT_ENABLED"),
        ("_test_gen_job", "AUTO_TEST_GEN_ENABLED"),
    ],
)
async def test_every_scheduled_job_has_an_explicit_kill_switch(
    monkeypatch: pytest.MonkeyPatch, job_name: str, flag_name: str
) -> None:
    import config
    from scripts import scheduler

    monkeypatch.setattr(config, flag_name, False)
    job = getattr(scheduler, job_name)
    implementation = getattr(job, "__wrapped__", job)
    with patch.object(builtins, "__import__", side_effect=AssertionError("job import reached")):
        result = await implementation()
    assert result["status"] == "skipped"

"""Programme fitness persistant, progression et relances proactives."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture
def program_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "fitness-program.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def test_default_program_is_seeded_with_pullup_bar(program_db: Path) -> None:
    from app.fitness.services import fitness_service

    program = fitness_service.get_program()

    assert len(program.sessions) == 4
    assert [session.day_of_week for session in program.sessions] == [0, 1, 3, 4]
    pull = program.sessions[1]
    assert pull.title == "Tirage avec barre"
    assert [exercise.name for exercise in pull.exercises] == [
        "Tractions pronation",
        "Tractions supination",
        "Suspension active",
        "Rows sous table",
        "Relevés de jambes suspendu",
    ]
    assert pull.stretches


def test_interactive_progress_updates_dashboard_and_summary(program_db: Path) -> None:
    from app.fitness.models import SessionProgressUpdate
    from app.fitness.services import fitness_service

    friday = date(2026, 7, 31)
    before = fitness_service.dashboard(friday)
    assert before.scheduled_session is not None
    session = before.scheduled_session

    in_progress = fitness_service.update_session_progress(
        session.id,
        SessionProgressUpdate.model_validate(
            {
                "date": friday,
                "status": "in_progress",
                "exercise_results": [
                    {"name": session.exercises[0].name, "completed": True}
                ],
            }
        ),
    )
    assert in_progress.exercise_results[0].completed is True
    assert fitness_service.dashboard(friday).summary.workout_done is False

    fitness_service.set_scheduled_session_status("done", friday)
    after = fitness_service.dashboard(friday)
    assert after.progress is not None and after.progress.status.value == "done"
    assert after.summary.workout_done is True
    assert after.weekly_done == 1


def test_weekly_count_and_streak_share_the_same_legacy_migration_rule(
    program_db: Path,
) -> None:
    from app.fitness.models import SessionProgressUpdate
    from app.fitness.services import fitness_service
    from database import fitness as fitness_repository

    previous_monday = date(2026, 8, 3)
    current_monday = date(2026, 8, 10)
    session_id = fitness_service.get_program().sessions[0].id
    fitness_service.update_session_progress(
        session_id,
        SessionProgressUpdate(
            date=previous_monday,
            status="done",
            exercise_results=[],
        ),
    )
    for day in (4, 5, 6):
        fitness_repository.create_workout(
            log_date=date(2026, 8, day).isoformat(),
            workout_type="autre",
            exercises_json=None,
            duration_min=45,
            source="pwa",
        )

    assert fitness_repository.weekly_done_count(previous_monday.isoformat()) == 4
    assert fitness_repository.current_week_streak(current_monday.isoformat(), 4) == 1


def test_fitness_timezone_comes_from_runtime_config(
    program_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config
    from app.fitness.services import configured_timezone

    monkeypatch.setattr(config, "TIMEZONE", "Pacific/Kiritimati")
    assert configured_timezone().key == "Pacific/Kiritimati"


@pytest.mark.asyncio
async def test_advice_fallback_is_explicit_and_logged(
    program_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import llm
    from app.fitness.services import fitness_service

    async def unavailable(*args, **kwargs):
        raise RuntimeError("provider offline")

    monkeypatch.setattr(llm, "chat", unavailable)
    caplog.set_level(logging.WARNING, logger="app.fitness.services")

    result = await fitness_service.advice(date(2026, 8, 3))

    assert result.source == "fallback"
    assert result.text
    assert "code=FITNESS_ADVICE_FALLBACK" in caplog.text


def test_program_and_session_edits_are_persistent(program_db: Path) -> None:
    from app.fitness.models import FitnessProgramUpdate, ProgramSessionUpdate
    from app.fitness.services import fitness_service

    program = fitness_service.update_program(
        FitnessProgramUpdate(
            calories_min=3200,
            calories_max=3700,
            reminder_time="17:30",
            reminder_interval_min=90,
        )
    )
    first = fitness_service.update_program_session(
        program.sessions[0].id,
        ProgramSessionUpdate(
            day_of_week=2,
            title="Poussée adaptée",
            exercises=[{"name": "Pompes tempo", "sets": 4, "reps": "10-12"}],
        ),
    )

    reloaded = fitness_service.get_program()
    assert reloaded.calories_min == 3200
    assert reloaded.reminder_time == "17:30"
    assert first.day_of_week == 2
    assert reloaded.sessions[0].exercises[0].name == "Pompes tempo"


def test_workout_reminder_repeats_on_cadence_then_stops_when_done(
    program_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config
    from app.fitness.models import FitnessProgramUpdate
    from app.fitness.services import fitness_service
    from jarvis.notification_service import notification_service
    from scripts.fitness_reminders import run_fitness_reminders

    monkeypatch.setattr(config, "FITNESS_REMINDERS_ENABLED", True)
    monkeypatch.setattr(config, "is_quiet_hours", lambda _now=None: False)
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        notification_service,
        "create",
        lambda **kwargs: sent.append(kwargs) or len(sent),
    )
    fitness_service.update_program(
        FitnessProgramUpdate(
            reminder_time="18:00",
            reminder_interval_min=120,
            meal_tracking_enabled=False,
        )
    )
    tz = ZoneInfo("Europe/Paris")

    assert run_fitness_reminders(datetime(2026, 8, 3, 18, 0, tzinfo=tz))["workout"]
    assert not run_fitness_reminders(datetime(2026, 8, 3, 18, 30, tzinfo=tz))["workout"]
    assert run_fitness_reminders(datetime(2026, 8, 3, 20, 0, tzinfo=tz))["workout"]
    fitness_service.set_scheduled_session_status("done", date(2026, 8, 3))
    assert not run_fitness_reminders(datetime(2026, 8, 3, 22, 0, tzinfo=tz))["workout"]
    assert len(sent) == 2
    assert all(item["priority"] == "high" for item in sent)


def test_meal_questions_are_sent_once_per_missing_slot(
    program_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config
    from app.fitness.models import FitnessProgramUpdate
    from app.fitness.services import fitness_service
    from jarvis.notification_service import notification_service
    from scripts.fitness_reminders import run_fitness_reminders

    monkeypatch.setattr(config, "FITNESS_REMINDERS_ENABLED", True)
    monkeypatch.setattr(config, "is_quiet_hours", lambda _now=None: False)
    sent: list[str] = []
    monkeypatch.setattr(
        notification_service,
        "create",
        lambda **kwargs: sent.append(str(kwargs["content"])) or len(sent),
    )
    fitness_service.update_program(
        FitnessProgramUpdate(reminders_enabled=False, meal_tracking_enabled=True)
    )
    tz = ZoneInfo("Europe/Paris")

    first = run_fitness_reminders(datetime(2026, 8, 3, 20, 30, tzinfo=tz))
    second = run_fitness_reminders(datetime(2026, 8, 3, 21, 0, tzinfo=tz))

    assert first["meal"] == ["dejeuner", "diner"]
    assert second["meal"] == []
    assert len(sent) == 2

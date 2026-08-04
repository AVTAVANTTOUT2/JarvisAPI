"""Relances proactives Fitness, persistantes et interrompues par validation.

Le scheduler appelle ce module toutes les 30 minutes. La cadence réelle vient
du programme SQLite : une séance reste rappelée jusqu'à ``done`` ou ``skipped``.
Les questions de repas ne partent qu'une fois par créneau et par jour.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import config
from app.fitness.services import configured_timezone, fitness_service
from database import fitness as fitness_repository
from jarvis.notification_service import notification_service

logger = logging.getLogger(__name__)


def _minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def _can_prompt(*, now: datetime, kind: str, reference: str, cooldown_min: int) -> bool:
    last = fitness_repository.get_last_prompt(now.date().isoformat(), kind, reference)
    return last is None or now.replace(tzinfo=None) - last >= timedelta(
        minutes=cooldown_min
    )


def _notify(
    now: datetime, *, kind: str, reference: str, title: str, content: str
) -> None:
    notification_service.create(
        source="fitness",
        title=title,
        content=content,
        priority="high",
        deduplication_window_seconds=0,
    )
    fitness_repository.record_prompt(now.date().isoformat(), kind, reference, now)


def run_fitness_reminders(now: datetime | None = None) -> dict[str, list[str]]:
    """Émet les rappels dus et retourne les catégories déclenchées."""
    timezone = configured_timezone()
    local_now = now or datetime.now(timezone)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=timezone)
    else:
        local_now = local_now.astimezone(timezone)
    emitted: dict[str, list[str]] = {"workout": [], "meal": []}
    if not getattr(config, "FITNESS_REMINDERS_ENABLED", True):
        return emitted
    if config.is_quiet_hours(local_now):
        return emitted
    try:
        from database import is_dnd_active

        if is_dnd_active():
            return emitted
    except Exception:
        pass

    program = fitness_service.get_program()
    date_value = local_now.date().isoformat()
    minute_of_day = local_now.hour * 60 + local_now.minute

    if program.reminders_enabled and minute_of_day >= _minutes(program.reminder_time):
        session = fitness_repository.get_scheduled_session(date_value)
        if session is not None:
            progress = fitness_repository.get_session_progress(
                int(session["id"]), date_value
            )
            status = progress["status"] if progress else "planned"
            reference = str(session["id"])
            if status not in {"done", "skipped"} and _can_prompt(
                now=local_now,
                kind="workout",
                reference=reference,
                cooldown_min=program.reminder_interval_min,
            ):
                _notify(
                    local_now,
                    kind="workout",
                    reference=reference,
                    title="JARVIS — Séance non faite",
                    content=(
                        f"Monsieur, la séance {session['title']} n'est toujours pas marquée "
                        "comme faite. Souhaitez-vous la commencer maintenant ?"
                    ),
                )
                emitted["workout"].append(reference)

    if program.meal_tracking_enabled:
        meal_slots = (
            ("dejeuner", 13 * 60 + 30, "Qu'avez-vous mangé à déjeuner, Monsieur ?"),
            ("diner", 20 * 60 + 30, "Qu'avez-vous mangé ce soir, Monsieur ?"),
        )
        for meal_type, due_minute, question in meal_slots:
            if minute_of_day < due_minute:
                continue
            if fitness_repository.has_meal_type(date_value, meal_type):
                continue
            if not _can_prompt(
                now=local_now,
                kind="meal",
                reference=meal_type,
                cooldown_min=24 * 60,
            ):
                continue
            _notify(
                local_now,
                kind="meal",
                reference=meal_type,
                title="JARVIS — Suivi alimentation",
                content=question,
            )
            emitted["meal"].append(meal_type)

    logger.info("[fitness] rappels émis : %s", emitted)
    return emitted

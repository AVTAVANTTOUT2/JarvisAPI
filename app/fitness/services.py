"""Logique métier du module fitness, indépendante de FastAPI."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from database import fitness as fitness_repository

from .models import (
    MealCreate,
    MealRead,
    TodaySummary,
    WaterCreate,
    WaterCreateResponse,
    WaterRead,
    WaterToday,
    WellbeingCreate,
    WellbeingRead,
    WorkoutCreate,
    WorkoutRead,
)

LOCAL_TIMEZONE = ZoneInfo("Europe/Paris")


def current_local_date() -> date:
    """Retourne la date civile utilisée par JARVIS sur le Mac Mini."""
    return datetime.now(LOCAL_TIMEZONE).date()


def _range_values(
    from_date: date | None,
    to_date: date | None,
) -> tuple[str | None, str | None]:
    """Valide et sérialise une plage de dates inclusive."""
    if from_date is not None and to_date is not None and from_date > to_date:
        raise ValueError("from doit être antérieur ou égal à to")
    return (
        from_date.isoformat() if from_date is not None else None,
        to_date.isoformat() if to_date is not None else None,
    )


class FitnessService:
    """Orchestre validation métier et repository SQLite fitness."""

    def create_workout(self, payload: WorkoutCreate) -> WorkoutRead:
        """Crée une séance."""
        exercises = (
            [item.model_dump(mode="json") for item in payload.exercises_json]
            if payload.exercises_json is not None
            else None
        )
        row = fitness_repository.create_workout(
            log_date=payload.date.isoformat(),
            workout_type=payload.type.value,
            exercises_json=exercises,
            duration_min=payload.duration_min,
            source=payload.source.value,
        )
        return WorkoutRead.model_validate(row)

    def list_workouts(
        self,
        from_date: date | None,
        to_date: date | None,
    ) -> list[WorkoutRead]:
        """Retourne l'historique des séances."""
        start, end = _range_values(from_date, to_date)
        return [
            WorkoutRead.model_validate(row)
            for row in fitness_repository.list_workouts(
                from_date=start,
                to_date=end,
            )
        ]

    def create_meal(self, payload: MealCreate) -> MealRead:
        """Crée un repas."""
        row = fitness_repository.create_meal(
            log_date=payload.date.isoformat(),
            meal_type=payload.meal_type.value
            if payload.meal_type is not None
            else None,
            description=payload.description,
            calories_estimate=payload.calories_estimate,
            source=payload.source.value,
        )
        return MealRead.model_validate(row)

    def list_meals(self, log_date: date) -> list[MealRead]:
        """Retourne les repas d'une date."""
        return [
            MealRead.model_validate(row)
            for row in fitness_repository.list_meals_for_date(log_date.isoformat())
        ]

    def create_water(self, payload: WaterCreate) -> WaterCreateResponse:
        """Ajoute une quantité d'eau et retourne le cumul de la date."""
        row = fitness_repository.create_water_intake(
            log_date=payload.date.isoformat(),
            amount_ml=payload.amount_ml,
            source=payload.source.value,
        )
        return WaterCreateResponse(
            water=WaterRead.model_validate(row),
            total_today_ml=fitness_repository.get_water_total(payload.date.isoformat()),
        )

    def water_today(self, today: date | None = None) -> WaterToday:
        """Retourne le cumul d'eau du jour local."""
        local_today = today or current_local_date()
        return WaterToday(
            date=local_today,
            amount_ml=fitness_repository.get_water_total(local_today.isoformat()),
        )

    def create_wellbeing(self, payload: WellbeingCreate) -> WellbeingRead:
        """Crée une note ou entrée de journal de bien-être."""
        row = fitness_repository.create_wellbeing_log(
            log_date=payload.date.isoformat(),
            rating=payload.rating,
            journal_text=payload.journal_text,
            source=payload.source.value,
        )
        return WellbeingRead.model_validate(row)

    def list_wellbeing(
        self,
        from_date: date | None,
        to_date: date | None,
    ) -> list[WellbeingRead]:
        """Retourne l'historique de bien-être."""
        start, end = _range_values(from_date, to_date)
        return [
            WellbeingRead.model_validate(row)
            for row in fitness_repository.list_wellbeing_logs(
                from_date=start,
                to_date=end,
            )
        ]

    def summary_today(self, today: date | None = None) -> TodaySummary:
        """Retourne la vue agrégée du jour local."""
        local_today = today or current_local_date()
        return TodaySummary.model_validate(
            fitness_repository.get_today_summary(local_today.isoformat())
        )


fitness_service = FitnessService()

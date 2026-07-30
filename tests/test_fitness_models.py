"""Validation stricte des contrats Pydantic du module fitness."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.fitness.models import (
    MealCreate,
    WaterCreate,
    WellbeingCreate,
    WorkoutCreate,
)


def test_workout_accepts_strict_valid_payload() -> None:
    workout = WorkoutCreate.model_validate_json(
        """
        {
          "date": "2026-07-30",
          "type": "jambes",
          "exercises_json": [
            {"name": "Squat", "sets": 4, "reps": 8, "weight_kg": 80.0}
          ],
          "duration_min": 55,
          "source": "pwa"
        }
        """
    )

    assert workout.date == date(2026, 7, 30)
    assert workout.type.value == "jambes"
    assert workout.exercises_json
    assert workout.exercises_json[0].name == "Squat"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "dos"),
        ("source", "desktop"),
        ("date", "30/07/2026"),
        ("duration_min", 0),
    ],
)
def test_workout_rejects_invalid_enums_dates_and_duration(
    field: str, value: object
) -> None:
    payload = {
        "date": "2026-07-30",
        "type": "jambes",
        "duration_min": 45,
        "source": "pwa",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        WorkoutCreate.model_validate_json(__import__("json").dumps(payload))


def test_models_forbid_unknown_fields_and_coercion() -> None:
    with pytest.raises(ValidationError):
        MealCreate.model_validate_json(
            """
            {
              "date": "2026-07-30",
              "description": "Salade",
              "source": "pwa",
              "unexpected": true
            }
            """
        )

    with pytest.raises(ValidationError):
        WaterCreate(
            date=date(2026, 7, 30),
            amount_ml="250",  # type: ignore[arg-type]
            source="pwa",
        )


def test_wellbeing_requires_rating_or_non_empty_journal() -> None:
    with pytest.raises(ValidationError):
        WellbeingCreate(
            date=date(2026, 7, 30),
            rating=None,
            journal_text="   ",
            source="pwa",
        )

    rating_only = WellbeingCreate(
        date=date(2026, 7, 30),
        rating=8,
        source="pwa",
    )
    journal_only = WellbeingCreate(
        date=date(2026, 7, 30),
        journal_text="Je me sens en forme.",
        source="pwa",
    )

    assert rating_only.rating == 8
    assert journal_only.journal_text == "Je me sens en forme."


@pytest.mark.parametrize("rating", [0, 11])
def test_wellbeing_rating_is_bounded(rating: int) -> None:
    with pytest.raises(ValidationError):
        WellbeingCreate(
            date=date(2026, 7, 30),
            rating=rating,
            source="voice",
        )

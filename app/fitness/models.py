"""Contrats Pydantic stricts du domaine fitness."""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
]


def _parse_iso_date(value: object) -> object:
    """Convertit exclusivement une date ISO ``YYYY-MM-DD`` issue du JSON."""
    if isinstance(value, str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return value
        try:
            return date.fromisoformat(value)
        except ValueError:
            return value
    return value


IsoDate = Annotated[date, BeforeValidator(_parse_iso_date)]


class WorkoutType(str, Enum):
    """Types de séances acceptés par le contrat public."""

    POUSSEE = "poussee"
    TIRAGE = "tirage"
    JAMBES = "jambes"
    FULL_BODY = "full_body"
    NATATION = "natation"
    AUTRE = "autre"


class MealType(str, Enum):
    """Moments de repas acceptés."""

    PETIT_DEJ = "petit_dej"
    DEJEUNER = "dejeuner"
    DINER = "diner"
    COLLATION = "collation"


class FitnessSource(str, Enum):
    """Origines autorisées pour un log fitness."""

    VOICE = "voice"
    PWA = "pwa"


class StrictModel(BaseModel):
    """Base commune : champs inconnus et coercitions silencieuses interdits."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class ExerciseDetail(StrictModel):
    """Détail structuré optionnel d'un exercice."""

    name: ShortText
    sets: int | None = Field(default=None, ge=1, le=100)
    reps: int | None = Field(default=None, ge=1, le=1_000)
    weight_kg: float | None = Field(default=None, ge=0, le=1_000)
    notes: NonEmptyText | None = None


class WorkoutCreate(StrictModel):
    """Commande de création d'une séance."""

    date: IsoDate
    type: WorkoutType = Field(strict=False)
    exercises_json: list[ExerciseDetail] | None = None
    duration_min: int | None = Field(default=None, ge=1, le=1_440)
    source: FitnessSource = Field(strict=False)


class MealCreate(StrictModel):
    """Commande de création d'un repas."""

    date: IsoDate
    meal_type: MealType | None = Field(default=None, strict=False)
    description: NonEmptyText
    calories_estimate: int | None = Field(default=None, ge=0, le=20_000)
    source: FitnessSource = Field(strict=False)


class WaterCreate(StrictModel):
    """Commande d'ajout incrémental d'eau."""

    date: IsoDate
    amount_ml: int = Field(ge=1, le=20_000)
    source: FitnessSource = Field(strict=False)


class WellbeingCreate(StrictModel):
    """Commande de journalisation du bien-être."""

    date: IsoDate
    rating: int | None = Field(default=None, ge=1, le=10)
    journal_text: NonEmptyText | None = None
    source: FitnessSource = Field(strict=False)

    @model_validator(mode="after")
    def require_rating_or_journal(self) -> WellbeingCreate:
        """Refuse un log vide tout en autorisant chaque mode indépendamment."""
        if self.rating is None and not self.journal_text:
            raise ValueError("rating ou journal_text est requis")
        return self


class WorkoutRead(WorkoutCreate):
    """Séance persistée."""

    id: int
    created_at: datetime


class MealRead(MealCreate):
    """Repas persisté."""

    id: int
    created_at: datetime


class WaterRead(WaterCreate):
    """Ajout d'eau persisté."""

    id: int
    created_at: datetime


class WellbeingRead(WellbeingCreate):
    """Log de bien-être persisté."""

    id: int
    created_at: datetime


class WorkoutHistory(StrictModel):
    """Réponse de l'historique des séances."""

    workouts: list[WorkoutRead]


class MealHistory(StrictModel):
    """Réponse des repas d'une journée."""

    meals: list[MealRead]


class WaterCreateResponse(StrictModel):
    """Résultat d'un ajout d'eau avec cumul du jour."""

    water: WaterRead
    total_today_ml: int


class WaterToday(StrictModel):
    """Cumul d'eau pour la date locale courante."""

    date: IsoDate
    amount_ml: int


class WellbeingHistory(StrictModel):
    """Réponse de l'historique de bien-être."""

    wellbeing: list[WellbeingRead]


class WellbeingSummary(StrictModel):
    """Dernier état de bien-être inclus dans un résumé."""

    rating: int | None = Field(default=None, ge=1, le=10)
    journal_text: str | None = None


class TodaySummary(StrictModel):
    """Vue agrégée consommée par la PWA et la voix."""

    date: IsoDate
    workout_done: bool
    workout_count: int
    meal_count: int
    calories_estimate: int
    water_ml: int
    wellbeing: WellbeingSummary | None

"""Contrats Pydantic stricts du domaine fitness."""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Literal

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


class SessionStatus(str, Enum):
    """État journalier d'une séance programmée."""

    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"


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


class ProgramExercise(StrictModel):
    """Prescription modifiable d'un exercice, échauffement ou étirement."""

    name: ShortText
    sets: int | None = Field(default=None, ge=1, le=100)
    reps: ShortText | None = None
    duration_sec: int | ShortText | None = Field(default=None)
    sides: int | None = Field(default=None, ge=1, le=2)
    progression: NonEmptyText | None = None


class ExerciseResult(StrictModel):
    """Réalisation concrète d'un exercice pendant une séance."""

    name: ShortText
    completed: bool = False
    sets_done: int | None = Field(default=None, ge=0, le=100)
    reps_done: ShortText | None = None
    duration_sec: int | None = Field(default=None, ge=0, le=7_200)
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
    protein_g: float | None = Field(default=None, ge=0, le=1_000)
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


class FitnessProgramUpdate(StrictModel):
    """Réglages généraux modifiables du programme actif."""

    name: ShortText | None = None
    goal: NonEmptyText | None = None
    weekly_min_sessions: int | None = Field(default=None, ge=1, le=7)
    calories_min: int | None = Field(default=None, ge=0, le=20_000)
    calories_max: int | None = Field(default=None, ge=0, le=20_000)
    protein_min_g: int | None = Field(default=None, ge=0, le=1_000)
    protein_max_g: int | None = Field(default=None, ge=0, le=1_000)
    reminders_enabled: bool | None = None
    reminder_time: Annotated[
        str,
        StringConstraints(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"),
    ] | None = None
    reminder_interval_min: int | None = Field(default=None, ge=30, le=720)
    meal_tracking_enabled: bool | None = None

    @model_validator(mode="after")
    def validate_target_ranges(self) -> FitnessProgramUpdate:
        if (
            self.calories_min is not None
            and self.calories_max is not None
            and self.calories_min > self.calories_max
        ):
            raise ValueError("calories_min doit être inférieur ou égal à calories_max")
        if (
            self.protein_min_g is not None
            and self.protein_max_g is not None
            and self.protein_min_g > self.protein_max_g
        ):
            raise ValueError("protein_min_g doit être inférieur ou égal à protein_max_g")
        return self


class ProgramSessionUpdate(StrictModel):
    """Modification d'une séance du programme."""

    day_of_week: int | None = Field(default=None, ge=0, le=6)
    title: ShortText | None = None
    description: NonEmptyText | None = None
    warmup: list[ProgramExercise] | None = None
    exercises: list[ProgramExercise] | None = None
    stretches: list[ProgramExercise] | None = None
    notes: NonEmptyText | None = None
    active: bool | None = None


class ProgramSessionRead(StrictModel):
    """Séance persistée dans le programme actif."""

    id: int
    position: int
    day_of_week: int = Field(ge=0, le=6)
    type: WorkoutType = Field(strict=False)
    title: str
    description: str | None
    warmup: list[ProgramExercise]
    exercises: list[ProgramExercise]
    stretches: list[ProgramExercise]
    notes: str | None
    active: bool


class FitnessProgramRead(StrictModel):
    """Programme complet et objectifs nutritionnels."""

    id: int
    name: str
    goal: str
    weekly_min_sessions: int
    calories_min: int
    calories_max: int
    protein_min_g: int
    protein_max_g: int
    reminders_enabled: bool
    reminder_time: str
    reminder_interval_min: int
    meal_tracking_enabled: bool
    sessions: list[ProgramSessionRead]
    updated_at: datetime


class SessionProgressUpdate(StrictModel):
    """Validation interactive d'une séance et de ses exercices."""

    date: IsoDate
    status: SessionStatus = Field(strict=False)
    exercise_results: list[ExerciseResult] = Field(default_factory=list)
    duration_min: int | None = Field(default=None, ge=1, le=1_440)
    perceived_effort: int | None = Field(default=None, ge=1, le=10)
    notes: NonEmptyText | None = None


class SessionProgressRead(SessionProgressUpdate):
    id: int
    program_session_id: int
    completed_at: datetime | None
    updated_at: datetime


class WeightCreate(StrictModel):
    date: IsoDate
    weight_kg: float = Field(gt=20, le=500)
    notes: NonEmptyText | None = None
    source: FitnessSource = Field(strict=False)


class WeightRead(WeightCreate):
    id: int
    created_at: datetime


class WeightHistory(StrictModel):
    weights: list[WeightRead]


class DailyFitnessDashboard(StrictModel):
    """État complet d'une journée, commun aux écrans et aux conseils."""

    date: IsoDate
    program: FitnessProgramRead
    scheduled_session: ProgramSessionRead | None
    progress: SessionProgressRead | None
    summary: TodaySummary
    weekly_done: int
    weekly_target: int
    current_streak_weeks: int
    next_session: ProgramSessionRead | None
    meals: list[MealRead]
    latest_weight: WeightRead | None


class FitnessAdvice(StrictModel):
    text: str
    source: Literal["ai", "fallback"]
    generated_at: datetime

"""Routes FastAPI du module fitness."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from .models import (
    MealCreate,
    MealHistory,
    MealRead,
    TodaySummary,
    WaterCreate,
    WaterCreateResponse,
    WaterToday,
    WellbeingCreate,
    WellbeingHistory,
    WellbeingRead,
    WorkoutCreate,
    WorkoutHistory,
    WorkoutRead,
)
from .services import fitness_service

router = APIRouter(prefix="/api/fitness", tags=["fitness"])


@router.post(
    "/workouts",
    response_model=WorkoutRead,
    status_code=status.HTTP_201_CREATED,
)
def create_workout(payload: WorkoutCreate) -> WorkoutRead:
    """Crée une séance."""
    return fitness_service.create_workout(payload)


@router.get("/workouts", response_model=WorkoutHistory)
def get_workouts(
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
) -> WorkoutHistory:
    """Retourne l'historique des séances dans une plage inclusive."""
    try:
        workouts = fitness_service.list_workouts(from_date, to_date)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return WorkoutHistory(workouts=workouts)


@router.post(
    "/meals",
    response_model=MealRead,
    status_code=status.HTTP_201_CREATED,
)
def create_meal(payload: MealCreate) -> MealRead:
    """Crée un repas."""
    return fitness_service.create_meal(payload)


@router.get("/meals", response_model=MealHistory)
def get_meals(log_date: Annotated[date, Query(alias="date")]) -> MealHistory:
    """Retourne les repas d'une date."""
    return MealHistory(meals=fitness_service.list_meals(log_date))


@router.post(
    "/water",
    response_model=WaterCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_water(payload: WaterCreate) -> WaterCreateResponse:
    """Ajoute une quantité d'eau."""
    return fitness_service.create_water(payload)


@router.get("/water/today", response_model=WaterToday)
def get_water_today() -> WaterToday:
    """Retourne le cumul d'eau du jour local."""
    return fitness_service.water_today()


@router.post(
    "/wellbeing",
    response_model=WellbeingRead,
    status_code=status.HTTP_201_CREATED,
)
def create_wellbeing(payload: WellbeingCreate) -> WellbeingRead:
    """Crée une note ou une entrée de journal de bien-être."""
    return fitness_service.create_wellbeing(payload)


@router.get("/wellbeing", response_model=WellbeingHistory)
def get_wellbeing(
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
) -> WellbeingHistory:
    """Retourne l'historique de bien-être dans une plage inclusive."""
    try:
        wellbeing = fitness_service.list_wellbeing(from_date, to_date)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return WellbeingHistory(wellbeing=wellbeing)


@router.get("/summary/today", response_model=TodaySummary)
def get_today_summary() -> TodaySummary:
    """Retourne la synthèse fitness du jour local."""
    return fitness_service.summary_today()

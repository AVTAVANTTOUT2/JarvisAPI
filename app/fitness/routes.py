"""Routes FastAPI du module fitness."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse

from jarvis.uploads import UploadRejected, resolve_managed_upload

from .meal_analysis import MealAnalysisError
from .models import (
    DailyFitnessDashboard,
    FitnessAdvice,
    FitnessProgramRead,
    FitnessProgramUpdate,
    FitnessSource,
    MealAnalysisPreview,
    MealCreate,
    MealHistory,
    MealRead,
    MealTextAnalyze,
    MealType,
    ProgramSessionRead,
    ProgramSessionUpdate,
    SessionProgressRead,
    SessionProgressUpdate,
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
    WeightCreate,
    WeightHistory,
    WeightRead,
)
from .services import fitness_service

router = APIRouter(prefix="/api/fitness", tags=["fitness"])


def _http_from_meal_error(error: MealAnalysisError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.detail)


def _http_from_upload_error(error: UploadRejected) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.detail)


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


@router.post("/meals/from-text", response_model=MealAnalysisPreview)
async def create_meal_from_text(payload: MealTextAnalyze) -> MealAnalysisPreview:
    """Structure un journal alimentaire libre via IA, puis l'enregistre."""
    try:
        return await fitness_service.create_meal_from_text(payload)
    except MealAnalysisError as error:
        raise _http_from_meal_error(error) from error


@router.post("/meals/from-photo", response_model=MealAnalysisPreview)
async def create_meal_from_photo(
    photo: Annotated[UploadFile, File(description="Photo de l'assiette")],
    log_date: Annotated[date, Form(alias="date")],
    source: Annotated[FitnessSource, Form()] = FitnessSource.PWA,
    meal_type: Annotated[MealType | None, Form()] = None,
    note: Annotated[str | None, Form()] = None,
    save: Annotated[bool, Form()] = True,
) -> MealAnalysisPreview:
    """Analyse une photo d'assiette (vision locale + estimation macros) et enregistre."""
    try:
        image_bytes = await photo.read()
        return await fitness_service.create_meal_from_photo(
            log_date=log_date,
            image_bytes=image_bytes,
            original_name=photo.filename,
            meal_type=meal_type,
            note=note,
            source_value=source.value,
            save=save,
        )
    except MealAnalysisError as error:
        raise _http_from_meal_error(error) from error
    except UploadRejected as error:
        raise _http_from_upload_error(error) from error


@router.get("/meals/{meal_id}/photo")
def get_meal_photo(meal_id: int) -> FileResponse:
    """Sert la photo privée d'un repas (session authentifiée requise)."""
    try:
        meal = fitness_service.get_meal(meal_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if not meal.photo_path:
        raise HTTPException(status_code=404, detail="Aucune photo pour ce repas")
    try:
        path = resolve_managed_upload(meal.photo_path)
    except UploadRejected as error:
        raise _http_from_upload_error(error) from error
    media_types: dict[str, str] = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    media_type = media_types.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        headers={"Cache-Control": "private, no-store"},
    )


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


@router.get("/program", response_model=FitnessProgramRead)
def get_program() -> FitnessProgramRead:
    """Retourne le programme actif, ses objectifs et ses rappels."""
    return fitness_service.get_program()


@router.patch("/program", response_model=FitnessProgramRead)
def update_program(payload: FitnessProgramUpdate) -> FitnessProgramRead:
    """Modifie les objectifs ou la politique de rappel du programme."""
    try:
        return fitness_service.update_program(payload)
    except (ValueError, LookupError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.patch("/program/sessions/{session_id}", response_model=ProgramSessionRead)
def update_program_session(
    session_id: int, payload: ProgramSessionUpdate
) -> ProgramSessionRead:
    """Modifie le planning, les exercices ou les étirements d'une séance."""
    try:
        return fitness_service.update_program_session(session_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/sessions/{session_id}/progress", response_model=SessionProgressRead)
def update_session_progress(
    session_id: int, payload: SessionProgressUpdate
) -> SessionProgressRead:
    """Crée ou remplace l'état interactif d'une séance pour une date."""
    try:
        return fitness_service.update_session_progress(session_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/dashboard", response_model=DailyFitnessDashboard)
def get_dashboard(
    target_date: Annotated[date | None, Query(alias="date")] = None,
) -> DailyFitnessDashboard:
    """Retourne l'état fitness complet du jour demandé."""
    return fitness_service.dashboard(target_date)


@router.post("/advice", response_model=FitnessAdvice)
async def generate_advice(
    target_date: Annotated[date | None, Query(alias="date")] = None,
) -> FitnessAdvice:
    """Génère à la demande un conseil IA contextualisé, avec repli hors ligne."""
    return await fitness_service.advice(target_date)


@router.post("/weights", response_model=WeightRead, status_code=status.HTTP_201_CREATED)
def create_weight(payload: WeightCreate) -> WeightRead:
    """Ajoute ou corrige la pesée d'une date."""
    return fitness_service.create_weight(payload)


@router.get("/weights", response_model=WeightHistory)
def get_weights(
    limit: Annotated[int, Query(ge=1, le=260)] = 52,
) -> WeightHistory:
    """Retourne l'historique hebdomadaire des pesées."""
    return WeightHistory(weights=fitness_service.list_weights(limit))

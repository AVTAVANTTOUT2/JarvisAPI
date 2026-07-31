"""Logique métier du module fitness, indépendante de FastAPI."""

from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from database import fitness as fitness_repository

from .models import (
    DailyFitnessDashboard,
    FitnessAdvice,
    FitnessProgramRead,
    FitnessProgramUpdate,
    MealCreate,
    MealRead,
    ProgramSessionRead,
    ProgramSessionUpdate,
    SessionProgressRead,
    SessionProgressUpdate,
    TodaySummary,
    WaterCreate,
    WaterCreateResponse,
    WaterRead,
    WaterToday,
    WellbeingCreate,
    WellbeingRead,
    WorkoutCreate,
    WorkoutRead,
    WeightCreate,
    WeightRead,
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
        meal_type = payload.meal_type.value if payload.meal_type is not None else None
        if meal_type is None and payload.date == current_local_date():
            hour = datetime.now(LOCAL_TIMEZONE).hour
            if 5 <= hour < 11:
                meal_type = "petit_dej"
            elif 11 <= hour < 16:
                meal_type = "dejeuner"
            elif 18 <= hour <= 23:
                meal_type = "diner"
            else:
                meal_type = "collation"
        row = fitness_repository.create_meal(
            log_date=payload.date.isoformat(),
            meal_type=meal_type,
            description=payload.description,
            calories_estimate=payload.calories_estimate,
            protein_g=payload.protein_g,
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

    def get_program(self) -> FitnessProgramRead:
        """Retourne le programme actif stocké en SQLite."""
        return FitnessProgramRead.model_validate(fitness_repository.get_active_program())

    def update_program(self, payload: FitnessProgramUpdate) -> FitnessProgramRead:
        values = payload.model_dump(exclude_none=True)
        return FitnessProgramRead.model_validate(
            fitness_repository.update_active_program(values)
        )

    def update_program_session(
        self, session_id: int, payload: ProgramSessionUpdate
    ) -> ProgramSessionRead:
        values = payload.model_dump(mode="json", exclude_none=True)
        return ProgramSessionRead.model_validate(
            fitness_repository.update_program_session(session_id, values)
        )

    def update_session_progress(
        self, session_id: int, payload: SessionProgressUpdate
    ) -> SessionProgressRead:
        exercise_results = [
            result.model_dump(mode="json") for result in payload.exercise_results
        ]
        row = fitness_repository.upsert_session_progress(
            session_id=session_id,
            log_date=payload.date.isoformat(),
            status=payload.status.value,
            exercise_results=exercise_results,
            duration_min=payload.duration_min,
            perceived_effort=payload.perceived_effort,
            notes=payload.notes,
        )
        return SessionProgressRead.model_validate(row)

    def complete_scheduled_session(self, today: date | None = None) -> SessionProgressRead:
        """Marque la séance prévue comme faite, notamment depuis la voix."""
        return self.set_scheduled_session_status("done", today)

    def set_scheduled_session_status(
        self, status: str, today: date | None = None
    ) -> SessionProgressRead:
        """Modifie l'état de la séance du jour sans perdre ses exercices cochés."""
        local_today = today or current_local_date()
        scheduled = fitness_repository.get_scheduled_session(local_today.isoformat())
        if scheduled is None:
            raise ValueError("Aucune séance n'est programmée aujourd'hui")
        existing = fitness_repository.get_session_progress(
            int(scheduled["id"]), local_today.isoformat()
        )
        results = existing["exercise_results"] if existing else []
        return self.update_session_progress(
            int(scheduled["id"]),
            SessionProgressUpdate(
                date=local_today,
                status=status,
                exercise_results=results,
            ),
        )

    def dashboard(self, target_date: date | None = None) -> DailyFitnessDashboard:
        local_date = target_date or current_local_date()
        date_value = local_date.isoformat()
        program = self.get_program()
        scheduled_raw = fitness_repository.get_scheduled_session(date_value)
        scheduled = (
            ProgramSessionRead.model_validate(scheduled_raw)
            if scheduled_raw is not None
            else None
        )
        progress_raw = (
            fitness_repository.get_session_progress(scheduled.id, date_value)
            if scheduled is not None
            else None
        )
        progress = (
            SessionProgressRead.model_validate(progress_raw)
            if progress_raw is not None
            else None
        )
        next_raw = fitness_repository.get_next_session(date_value)
        weights = fitness_repository.latest_weight()
        return DailyFitnessDashboard(
            date=local_date,
            program=program,
            scheduled_session=scheduled,
            progress=progress,
            summary=self.summary_today(local_date),
            weekly_done=fitness_repository.weekly_done_count(date_value),
            weekly_target=program.weekly_min_sessions,
            current_streak_weeks=fitness_repository.current_week_streak(
                date_value, program.weekly_min_sessions
            ),
            next_session=(
                ProgramSessionRead.model_validate(next_raw)
                if next_raw is not None
                else None
            ),
            meals=self.list_meals(local_date),
            latest_weight=(WeightRead.model_validate(weights) if weights else None),
        )

    def create_weight(self, payload: WeightCreate) -> WeightRead:
        return WeightRead.model_validate(
            fitness_repository.upsert_weight(
                log_date=payload.date.isoformat(),
                weight_kg=payload.weight_kg,
                notes=payload.notes,
                source=payload.source.value,
            )
        )

    def list_weights(self, limit: int = 52) -> list[WeightRead]:
        return [
            WeightRead.model_validate(row)
            for row in fitness_repository.list_weights(limit=limit)
        ]

    @staticmethod
    def _fallback_advice(dashboard: DailyFitnessDashboard) -> str:
        if dashboard.scheduled_session and (
            dashboard.progress is None or dashboard.progress.status.value != "done"
        ):
            return (
                f"La priorité du jour est la séance {dashboard.scheduled_session.title}. "
                "Commencez par l'échauffement, gardez deux répétitions propres en réserve "
                "et terminez par les étirements prévus."
            )
        if dashboard.summary.meal_count < 3:
            return (
                "La séance est couverte. Il manque surtout des apports réguliers : "
                "visez un prochain repas dense en énergie avec une source de protéines."
            )
        if dashboard.summary.water_ml < 1500:
            return "Hydratation encore légère aujourd'hui. Ajoutez progressivement 500 ml d'eau."
        return (
            "Journée cohérente. Conservez une exécution propre, récupérez et contrôlez "
            "la tendance du poids lors de la pesée hebdomadaire."
        )

    async def advice(self, target_date: date | None = None) -> FitnessAdvice:
        """Génère un conseil contextuel avec repli déterministe hors ligne."""
        dashboard = self.dashboard(target_date)
        fallback = self._fallback_advice(dashboard)
        try:
            import config
            import llm

            snapshot = {
                "date": dashboard.date.isoformat(),
                "session": (
                    dashboard.scheduled_session.title
                    if dashboard.scheduled_session
                    else None
                ),
                "status": (
                    dashboard.progress.status.value if dashboard.progress else "planned"
                ),
                "weekly_done": dashboard.weekly_done,
                "weekly_target": dashboard.weekly_target,
                "meals": dashboard.summary.meal_count,
                "calories_logged": dashboard.summary.calories_estimate,
                "water_ml": dashboard.summary.water_ml,
                "latest_weight_kg": (
                    dashboard.latest_weight.weight_kg
                    if dashboard.latest_weight
                    else None
                ),
            }
            result = await llm.chat(
                messages=[{"role": "user", "content": json.dumps(snapshot, ensure_ascii=False)}],
                model=config.DEEPSEEK_FAST_MODEL,
                system=(
                    "Tu es le coach fitness de JARVIS. Donne en français un conseil "
                    "personnalisé, concret et prudent en 2 à 4 phrases à partir des seules "
                    "données fournies. Programme poids du corps, objectif prise de masse. "
                    "Ne diagnostique rien, n'invente aucune donnée, ne culpabilise pas. "
                    "Si douleur, malaise ou symptôme apparaît dans les données, recommande "
                    "d'arrêter et de consulter un professionnel. Aucun emoji."
                ),
                max_tokens=180,
                temperature=0.35,
            )
            text = str(result.get("content") or "").strip()
            if text:
                return FitnessAdvice(
                    text=text,
                    source="ai",
                    generated_at=datetime.now(LOCAL_TIMEZONE),
                )
        except Exception:
            pass
        return FitnessAdvice(
            text=fallback,
            source="fallback",
            generated_at=datetime.now(LOCAL_TIMEZONE),
        )


fitness_service = FitnessService()

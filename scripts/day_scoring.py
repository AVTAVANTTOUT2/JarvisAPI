"""Détection de journée exceptionnelle + indice de chance — heuristique, pas de ML.

Deux scores 0-100 dérivés des mêmes signaux déjà en base (tâches, humeur,
activité écran), pondérés différemment et documentés de façon transparente
via le champ ``factors`` de chaque résultat :

- ``exceptional_score`` : poids fort sur l'accomplissement (tâches
  terminées, énergie, absence de retard) — « journée productive/positive ».
- ``luck_score`` : poids fort sur l'absence de friction (pas de retard, peu
  de distraction, humeur haute avec peu d'effort visible) — proxy de
  « la journée s'est bien passée sans forcer », PAS une mesure réelle de
  chance (aucune source de données ne permet de mesurer la chance).

Aucun des deux n'est un modèle entraîné : ce sont des formules fixes,
recalculables et vérifiables, jamais présentées comme plus qu'un indice.
"""

from __future__ import annotations

from datetime import datetime


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> int:
    return int(round(max(lo, min(hi, v))))


def compute_day_scores(day_data: dict) -> dict:
    """Calcule les deux scores à partir d'un résumé de journée (fonction pure).

    ``day_data`` attend : ``tasks_done`` (int), ``tasks_overdue`` (int),
    ``mood_score`` (1-10 ou None), ``energy_level`` (1-10 ou None),
    ``screen_mood_counts`` (dict ex. ``{"focused": 3, "distracted": 1}``).
    """
    tasks_done = int(day_data.get("tasks_done") or 0)
    tasks_overdue = int(day_data.get("tasks_overdue") or 0)
    mood_score = day_data.get("mood_score")
    energy_level = day_data.get("energy_level")
    screen_counts = day_data.get("screen_mood_counts") or {}
    total_screen = sum(screen_counts.values())
    focused_ratio = (screen_counts.get("focused", 0) / total_screen) if total_screen else 0.0
    distracted_ratio = (screen_counts.get("distracted", 0) / total_screen) if total_screen else 0.0

    factors: dict[str, float] = {}

    exceptional = 50.0
    factors["tasks_done_bonus"] = min(tasks_done, 4) * 8
    factors["tasks_overdue_penalty"] = -min(tasks_overdue, 3) * 10
    factors["mood_effect"] = (mood_score - 5) * 4 if mood_score is not None else 0.0
    factors["energy_effect"] = (energy_level - 5) * 2 if energy_level is not None else 0.0
    factors["focused_bonus"] = focused_ratio * 10
    factors["distracted_penalty"] = -distracted_ratio * 10
    exceptional += sum(factors.values())
    exceptional_score = _clamp(exceptional)

    luck_factors: dict[str, float] = {}
    luck = 50.0
    luck_factors["mood_effect"] = (mood_score - 5) * 5 if mood_score is not None else 0.0
    luck_factors["overdue_penalty"] = -tasks_overdue * 8
    luck_factors["smooth_day_bonus"] = 5.0 if (tasks_done > 0 and tasks_overdue == 0) else 0.0
    luck_factors["focused_bonus"] = focused_ratio * 15
    luck_factors["distracted_penalty"] = -distracted_ratio * 15
    luck += sum(luck_factors.values())
    luck_score = _clamp(luck)

    return {
        "exceptional_score": exceptional_score,
        "luck_score": luck_score,
        "factors": {"exceptional": factors, "luck": luck_factors},
    }


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _day_summary_for_scoring(date: str) -> dict:
    """Rassemble les signaux bruts d'une journée depuis la DB (SQL pur)."""
    from database import get_db

    with get_db() as conn:
        tasks_done = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = 'done' AND DATE(completed_at) = ?", (date,)
        ).fetchone()[0]
        tasks_overdue = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status != 'done' AND due_date IS NOT NULL "
            "AND DATE(due_date) = ?",
            (date,),
        ).fetchone()[0]
        mood_row = conn.execute(
            "SELECT mood_score, energy_level FROM mood_log WHERE DATE(created_at) = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (date,),
        ).fetchone()
        screen_rows = conn.execute(
            "SELECT mood, COUNT(*) AS c FROM screen_activity WHERE DATE(created_at) = ? "
            "AND mood IS NOT NULL GROUP BY mood",
            (date,),
        ).fetchall()
    return {
        "tasks_done": tasks_done,
        "tasks_overdue": tasks_overdue,
        "mood_score": mood_row["mood_score"] if mood_row else None,
        "energy_level": mood_row["energy_level"] if mood_row else None,
        "screen_mood_counts": {r["mood"]: r["c"] for r in screen_rows},
    }


def score_day(date: str | None = None, persist: bool = True) -> dict:
    """Calcule et (par défaut) persiste les scores du jour dans `day_scores`."""
    from database import upsert_day_score

    date = date or _today()
    summary = _day_summary_for_scoring(date)
    result = compute_day_scores(summary)
    if persist:
        upsert_day_score(
            date,
            exceptional_score=result["exceptional_score"],
            luck_score=result["luck_score"],
            factors=result["factors"],
        )
    return {"date": date, **result}

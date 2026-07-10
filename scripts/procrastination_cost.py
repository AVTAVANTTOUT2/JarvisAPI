"""Coût de la procrastination — heuristique sur `tasks`, aucun ML.

Une tâche est « procrastinée » quand elle est toujours `todo`/`doing` plus de
`config.PROCRASTINATION_ABANDONED_DAYS` jours après sa création. Le coût est
double :
- **temps mental** : chaque jour où une tâche traîne coûte une charge
  mentale estimée (``_MENTAL_OVERHEAD_MINUTES_PER_DAY``, hypothèse
  documentée et transparente, pas une mesure) — cumulée en heures.
- **coût monétaire** (optionnel) : si `config.PROCRASTINATION_HOURLY_VALUE`
  est configuré (> 0), les heures de charge mentale sont converties en coût
  estimé. Sinon `estimated_cost` reste `None` — jamais de chiffre inventé.
"""

from __future__ import annotations

from datetime import datetime

import config

_MENTAL_OVERHEAD_MINUTES_PER_DAY = 10.0


def _parse_dt(raw) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")[:19])
    except ValueError:
        return None


def compute_procrastination_cost(
    tasks: list[dict],
    now: datetime | None = None,
    abandoned_days: int | None = None,
    hourly_value: float | None = None,
) -> dict:
    """Analyse une liste de tâches (pure, testable sans DB).

    Retourne ``{abandoned_tasks, total_days_pending, overhead_hours,
    estimated_cost, explanation}``.
    """
    now = now or datetime.now()
    threshold = abandoned_days if abandoned_days is not None else config.PROCRASTINATION_ABANDONED_DAYS
    rate = hourly_value if hourly_value is not None else config.PROCRASTINATION_HOURLY_VALUE

    abandoned = []
    for t in tasks:
        if t.get("status") == "done":
            continue
        created = _parse_dt(t.get("created_at"))
        if created is None:
            continue
        days_pending = (now - created).days
        if days_pending < threshold:
            continue
        abandoned.append({
            "id": t.get("id"),
            "title": t.get("title"),
            "category": t.get("category"),
            "priority": t.get("priority"),
            "days_pending": days_pending,
        })

    abandoned.sort(key=lambda t: t["days_pending"], reverse=True)
    total_days_pending = sum(t["days_pending"] for t in abandoned)
    overhead_hours = round(total_days_pending * _MENTAL_OVERHEAD_MINUTES_PER_DAY / 60.0, 1)
    estimated_cost = round(overhead_hours * rate, 2) if rate and rate > 0 else None

    if not abandoned:
        explanation = "Aucune tâche laissée en plan au-delà du seuil."
    else:
        explanation = (
            f"{len(abandoned)} tâche(s) en attente depuis plus de {threshold} jours, "
            f"{total_days_pending} jours cumulés — soit environ {overhead_hours}h de charge mentale "
            f"(hypothèse : {_MENTAL_OVERHEAD_MINUTES_PER_DAY:.0f} min/jour de rappel non résolu)."
        )

    return {
        "abandoned_tasks": abandoned,
        "total_days_pending": total_days_pending,
        "overhead_hours": overhead_hours,
        "estimated_cost": estimated_cost,
        "explanation": explanation,
    }


def get_procrastination_cost() -> dict:
    """Point d'entrée réel — va chercher toutes les tâches non terminées via `get_tasks`."""
    from database import get_tasks

    tasks = get_tasks(status="all")
    return compute_procrastination_cost(tasks)

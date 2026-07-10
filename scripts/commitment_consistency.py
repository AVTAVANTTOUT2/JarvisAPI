"""Score de cohérence promesses/actions — étend le traqueur de `commitments`.

Mesure à quel point les engagements pris (« je t'envoie ça demain ») sont
effectivement tenus. Formule déterministe : ratio tenus/résolus sur la
fenêtre, pénalisé par les engagements ouverts en retard — aucun LLM,
aucun ML, un calcul reproductible sur les statuts déjà trackés par
`scripts/commitments.py`.
"""

from __future__ import annotations

from datetime import datetime

_OVERDUE_PENALTY_PER_ITEM = 5


def _parse_dt(raw) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")[:19])
    except ValueError:
        return None


def compute_consistency_score(
    commitments: list[dict],
    now: datetime | None = None,
    overdue_days: int = 3,
) -> dict:
    """Calcule le score à partir d'une liste de commitments (fonction pure).

    Chaque élément attend au moins ``status`` (``open``/``kept``/``dropped``)
    et ``created_at``.
    """
    now = now or datetime.now()
    kept = [c for c in commitments if c.get("status") == "kept"]
    dropped = [c for c in commitments if c.get("status") == "dropped"]
    open_ones = [c for c in commitments if c.get("status") == "open"]

    overdue_open = []
    for c in open_ones:
        created = _parse_dt(c.get("created_at"))
        if created and (now - created).days >= overdue_days:
            overdue_open.append(c)

    resolved_total = len(kept) + len(dropped)
    if resolved_total == 0:
        return {
            "score": None,
            "kept": 0, "dropped": 0, "open": len(open_ones), "overdue_open": len(overdue_open),
            "explanation": "Pas encore assez d'engagements résolus (tenus ou abandonnés) pour un score.",
        }

    ratio = len(kept) / resolved_total
    score = ratio * 100
    score -= min(len(overdue_open), 10) * _OVERDUE_PENALTY_PER_ITEM
    score = max(0, min(100, round(score)))

    explanation = (
        f"{len(kept)}/{resolved_total} engagement(s) tenu(s) ({round(ratio * 100)}%)"
        + (f", {len(overdue_open)} en retard non résolu(s)" if overdue_open else "")
        + "."
    )

    return {
        "score": score,
        "kept": len(kept), "dropped": len(dropped),
        "open": len(open_ones), "overdue_open": len(overdue_open),
        "explanation": explanation,
    }


def get_consistency_score(days: int = 90) -> dict:
    """Point d'entrée réel — commitments des `days` derniers jours, toutes statuts."""
    from database import get_db

    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM commitments WHERE created_at >= datetime('now', ?) "
            "ORDER BY created_at ASC",
            (f"-{int(days)} days",),
        ).fetchall()
        commitments = [dict(r) for r in rows]
    return compute_consistency_score(commitments)

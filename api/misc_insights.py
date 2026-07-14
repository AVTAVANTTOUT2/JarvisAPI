"""Handlers des indicateurs personnels et de la mémoire agrégée."""

from __future__ import annotations

import logging

from fastapi import HTTPException

import config
from database import get_all_people, get_life_profile, get_recent_episodes, get_school_documents

logger = logging.getLogger("jarvis")



async def api_productivity_score():
    """Score de productivité hebdomadaire (déterministe, 0-100)."""
    from scripts.rituals import compute_productivity_score

    return compute_productivity_score()


async def api_mood_signals(days: int = 14):
    """Signaux comportementaux quotidiens (écran + messages). Aucun diagnostic."""
    from database import get_mood_signals

    return {"signals": get_mood_signals(days)}


async def api_predictions_messages(limit: int = 20):
    """Prédiction heuristique de qui va écrire prochainement (iMessage)."""
    from scripts.message_predictor import predict_for_all_contacts

    return {"predictions": await predict_for_all_contacts(limit=limit)}


async def api_places_favorites(limit: int = 10):
    """Lieux les plus fréquentés (visit_count >= seuil configuré)."""
    from scripts.favorite_places import get_favorite_places

    return {"places": get_favorite_places(limit=limit)}


async def api_places_missed_opportunities():
    """Lieux favoris délaissés depuis plus de `OPPORTUNITY_MIN_DAYS_NAMED` jours."""
    from scripts.favorite_places import detect_missed_opportunities

    return {"opportunities": detect_missed_opportunities()}


async def api_doomscroll(days: int = 7):
    """Journées où le temps sur les apps à risque dépasse le seuil configuré."""
    from scripts.doomscroll_detector import detect_doomscrolling

    return {"days": detect_doomscrolling(days=days)}


async def api_procrastination_cost():
    """Coût (temps + estimation monétaire optionnelle) des tâches laissées en plan."""
    from scripts.procrastination_cost import get_procrastination_cost

    return get_procrastination_cost()


async def api_jarvis_journal(days: int = 7):
    """Journal de JARVIS — son point de vue sur les derniers jours."""
    from database import get_jarvis_journal_entries

    return {"entries": get_jarvis_journal_entries(days=days)}


async def api_jarvis_journal_generate(payload: dict | None = None):
    """Force la génération de l'entrée du jour (ou d'une date donnée)."""
    from scripts.jarvis_journal import generate_journal_entry

    date = (payload or {}).get("date")
    return await generate_journal_entry(date=date)


async def api_day_scores(metric: str = "exceptional_score", limit: int = 10, days: int = 90):
    """Top jours par score (jour exceptionnel ou indice de chance)."""
    from database import get_top_days

    if metric not in ("exceptional_score", "luck_score"):
        raise HTTPException(400, "metric ∈ {exceptional_score, luck_score}")
    return {"days": get_top_days(metric=metric, limit=limit, days=days)}


async def api_day_score_detail(date: str):
    """Score détaillé (exceptionnel + chance) d'une date donnée."""
    from database import get_day_score

    score = get_day_score(date)
    if not score:
        raise HTTPException(404, "Aucun score pour cette date")
    return score


async def api_presence():
    """Présence au bureau détectée par le son (micro daemon audio)."""
    from scripts.presence import get_today_sessions, presence_detector

    return {
        **presence_detector.get_status(),
        "today_sessions": get_today_sessions(),
    }


async def api_self_healing_status():
    """État du self-healing : activé ?, dernier patch, cooldown."""
    from scripts.self_healing import _load_state

    return {
        "enabled": config.SELF_HEALING_ENABLED,
        "auto_apply": config.SELF_HEALING_AUTO_APPLY,
        "state": _load_state(),
    }


async def api_self_healing_diagnose(body: dict = None):
    """Déclenche un diagnostic (+ patch si auto-apply) à la demande, sur un log fourni."""
    from scripts.self_healing import handle_crash_loop

    log_tail = (body or {}).get("log_tail", "")
    if not log_tail.strip():
        raise HTTPException(400, "Le champ 'log_tail' est requis.")
    return await handle_crash_loop(log_tail)



async def api_stats_compare():
    """Comparatif toi vs toi : cette semaine vs la précédente, ton neutre."""
    from database import get_week_comparison

    return get_week_comparison()


async def api_commitments_list(status: str = "open"):
    """Engagements pris par l'utilisateur (promesses traquées)."""
    from database import get_commitments

    if status not in ("open", "kept", "dropped"):
        raise HTTPException(400, "status ∈ {open, kept, dropped}")
    return {"commitments": get_commitments(status)}


async def api_commitments_update(commitment_id: int, body: dict):
    """Marque un engagement tenu ('kept') ou abandonné ('dropped')."""
    from database import update_commitment_status

    status = (body or {}).get("status")
    if status not in ("open", "kept", "dropped"):
        raise HTTPException(400, "status ∈ {open, kept, dropped}")
    if not update_commitment_status(commitment_id, status):
        raise HTTPException(404, f"Engagement #{commitment_id} introuvable")
    return {"ok": True, "id": commitment_id, "status": status}


async def api_commitments_consistency(days: int = 90):
    """Score de cohérence promesses/actions sur les `days` derniers jours."""
    from scripts.commitment_consistency import get_consistency_score

    return get_consistency_score(days=days)


async def api_meetings_list(limit: int = 10):
    """Réunions captées et résumées (table recordings, label 'réunion')."""
    from database import get_db

    lim = max(1, min(limit, 50))
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, title, created_at, duration_seconds, summary, actions_taken
               FROM recordings WHERE label = 'réunion'
               ORDER BY created_at DESC LIMIT ?""",
            (lim,),
        ).fetchall()
    return {"meetings": [dict(r) for r in rows]}


async def api_memory_get():
    """Retourne le life profile + les fiches people + documents école."""
    try:
        documents = get_school_documents(limit=50)
    except Exception as e:
        logger.error(f"Erreur get_school_documents : {e}")
        documents = []

    return {
        "life_profile": get_life_profile(),
        "people": get_all_people(),
        "recent_episodes": get_recent_episodes(limit=20),
        "school_documents": documents,
    }


# ── École : documents uploadés + fichiers produits ──────────


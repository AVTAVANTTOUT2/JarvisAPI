"""Routes des rituels et du mode ne pas déranger."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

router = APIRouter()
logger = logging.getLogger("jarvis")


@router.get("/api/rituals/today")
async def api_rituals_today():
    """Rituels du jour : roast, debrief, citation, score productivité."""
    from scripts.rituals import compute_productivity_score

    from database import get_daily_ritual

    row = get_daily_ritual(datetime.now().strftime("%Y-%m-%d")) or {}
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "roast": row.get("roast"),
        "debrief": row.get("debrief"),
        "quote": row.get("quote"),
        "weekly_debrief": row.get("weekly_debrief"),
        "productivity": compute_productivity_score(),
    }


@router.post("/api/rituals/{ritual}/run")
async def api_rituals_run(ritual: str):
    """Déclenche un rituel à la demande : roast, debrief, quote ou weekly."""
    from scripts import rituals

    runners = {
        "roast": rituals.daily_roast,
        "debrief": rituals.evening_debrief,
        "quote": rituals.daily_quote,
        "weekly": rituals.weekly_debrief,
    }
    fn = runners.get(ritual)
    if fn is None:
        raise HTTPException(404, f"Rituel inconnu : {ritual} (roast | debrief | quote)")
    try:
        return await fn()
    except Exception as e:
        logger.exception("rituel %s : %s", ritual, e)
        raise HTTPException(500, f"Rituel {ritual} échoué") from e



@router.get("/api/dnd")
async def api_dnd_status():
    """État du mode « silence total sauf feu »."""
    from database import get_dnd_status

    return get_dnd_status()


@router.post("/api/dnd")
async def api_dnd_enable(body: dict = None):
    """Active le DND. body: {\"minutes\": 120} (défaut 120). Seul l'urgent passe."""
    from database import set_dnd

    minutes = int((body or {}).get("minutes") or 120)
    minutes = max(1, min(minutes, 24 * 60))
    until = set_dnd(minutes)
    return {"active": True, "until": until}


@router.delete("/api/dnd")
async def api_dnd_disable():
    """Coupe le DND immédiatement."""
    from database import clear_dnd, get_dnd_status

    clear_dnd()
    return get_dnd_status()

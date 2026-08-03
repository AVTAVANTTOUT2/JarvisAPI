"""Routes des rituels et du mode ne pas déranger."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from database.time_buckets import local_datetime

router = APIRouter()
logger = logging.getLogger("jarvis")


class DndEnableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    minutes: int = Field(default=120, ge=1, le=24 * 60)


@router.get("/api/rituals/today")
async def api_rituals_today():
    """Rituels du jour : roast, debrief, citation, score productivité."""
    from database import get_daily_ritual
    from scripts.rituals import compute_productivity_score

    today = local_datetime().date().isoformat()
    row = get_daily_ritual(today) or {}
    return {
        "date": today,
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
async def api_dnd_enable(body: DndEnableRequest | None = None):
    """Active le DND. body: {\"minutes\": 120} (défaut 120). Seul l'urgent passe."""
    from database import set_dnd

    minutes = body.minutes if body is not None else 120
    until = set_dnd(minutes)
    return {"active": True, "until": until}


@router.delete("/api/dnd")
async def api_dnd_disable():
    """Coupe le DND immédiatement."""
    from database import clear_dnd, get_dnd_status

    clear_dnd()
    return get_dnd_status()

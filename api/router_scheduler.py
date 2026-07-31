"""Routes du suivi des jobs APScheduler (page /scheduler)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
logger = logging.getLogger("jarvis")


@router.get("/api/scheduler/jobs")
async def api_scheduler_jobs(days: int = Query(7, ge=1, le=30)):
    """Catalogue des jobs README + statut du jour + agrégats N jours."""
    from scripts.scheduler_status import build_scheduler_status

    return build_scheduler_status(days=days)


@router.get("/api/scheduler/jobs/{job_id}/runs")
async def api_scheduler_job_runs(
    job_id: str,
    days: int = Query(7, ge=1, le=30),
    limit: int = Query(100, ge=1, le=500),
):
    """Historique détaillé d'un job (détail au clic pour les ticks fréquents)."""
    from scripts.scheduler_status import build_job_runs

    try:
        return build_job_runs(job_id, days=days, limit=limit)
    except KeyError as exc:
        raise HTTPException(404, f"Job inconnu : {job_id}") from exc


@router.post("/api/scheduler/jobs/{job_id}/run")
async def api_scheduler_job_run(job_id: str):
    """Relance manuelle — refusée pour les ticks fréquents."""
    from scripts.scheduler import run_job_now

    try:
        return await run_job_now(job_id)
    except KeyError as exc:
        raise HTTPException(404, f"Job inconnu : {job_id}") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except Exception as exc:
        logger.exception("scheduler run %s : %s", job_id, exc)
        raise HTTPException(500, f"Exécution de {job_id} échouée") from exc

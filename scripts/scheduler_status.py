"""Assemblage du statut scheduler pour l'API / page /scheduler."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.scheduler_runs import aggregate_scheduler_runs, list_scheduler_runs
from database.time_buckets import local_datetime
from scripts.scheduler_tracking import (
    JOB_SPECS,
    derive_today_status,
    job_enabled,
)


def _iso_next_run(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.isoformat()
    return str(value)


def build_scheduler_status(*, days: int = 7) -> dict[str, Any]:
    """Catalogue README + agrégats + next_run APScheduler."""
    days_n = max(1, min(int(days), 30))
    aggregates = aggregate_scheduler_runs(days=days_n, job_ids=list(JOB_SPECS.keys()))

    next_runs: dict[str, str | None] = {}
    scheduler_running = False
    try:
        from scripts.scheduler import scheduler

        scheduler_running = bool(scheduler.running)
        for job in scheduler.get_jobs():
            next_runs[str(job.id)] = _iso_next_run(getattr(job, "next_run_time", None))
    except Exception:
        next_runs = {}

    jobs: list[dict[str, Any]] = []
    for job_id, spec in JOB_SPECS.items():
        agg = aggregates.get(job_id) or {}
        enabled = job_enabled(spec)
        today_count = int(agg.get("today_count") or 0)
        today_ok = int(agg.get("today_ok") or 0)
        today_error = int(agg.get("today_error") or 0)
        last_status = agg.get("last_status")
        today_status = derive_today_status(
            spec=spec,
            enabled=enabled,
            today_count=today_count,
            today_ok=today_ok,
            today_error=today_error,
            last_status=str(last_status) if last_status else None,
        )
        jobs.append(
            {
                "job_id": job_id,
                "title": spec.title,
                "description": spec.description,
                "cadence": spec.cadence,
                "group": spec.group,
                "schedule": spec.schedule_label,
                "enabled": enabled,
                "manual_run": bool(spec.manual_run and spec.cadence != "frequent"),
                "today_status": today_status,
                "next_run_at": next_runs.get(job_id),
                "last_run": {
                    "started_at": agg.get("last_started_at"),
                    "status": last_status,
                    "output": agg.get("last_output"),
                    "error": agg.get("last_error"),
                    "duration_ms": agg.get("last_duration_ms"),
                    "trigger": agg.get("last_trigger"),
                }
                if agg.get("last_started_at")
                else None,
                "stats": {
                    "days": days_n,
                    "total": int(agg.get("total") or 0),
                    "ok": int(agg.get("ok_count") or 0),
                    "error": int(agg.get("error_count") or 0),
                    "skipped": int(agg.get("skipped_count") or 0),
                    "silent": int(agg.get("silent_count") or 0),
                    "today": today_count,
                    "today_ok": today_ok,
                    "today_error": today_error,
                },
            }
        )

    order = {"daily": 0, "frequent": 1, "weekly": 2}
    jobs.sort(key=lambda j: (order.get(str(j["group"]), 9), str(j["schedule"]), str(j["job_id"])))

    return {
        "generated_at": local_datetime().isoformat(),
        "days": days_n,
        "scheduler_running": scheduler_running,
        "jobs": jobs,
    }


def build_job_runs(job_id: str, *, days: int = 7, limit: int = 100) -> dict[str, Any]:
    if job_id not in JOB_SPECS:
        raise KeyError(job_id)
    runs = list_scheduler_runs(job_id=job_id, days=days, limit=limit)
    return {
        "job_id": job_id,
        "days": max(1, min(int(days), 30)),
        "runs": runs,
    }

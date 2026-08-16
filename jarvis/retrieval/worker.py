"""Maintenance bornée et reprenable de la projection de connaissances."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import config
from database import init_db, list_user_profiles, use_profile
from database.knowledge import get_knowledge_observability, prune_knowledge_jobs

from .coordinator import (
    backfill_knowledge,
    process_knowledge_embeddings,
    process_knowledge_jobs,
)

logger = logging.getLogger(__name__)


def run_knowledge_maintenance_once(
    *,
    backfill_limit: int = 500,
    job_limit: int = 500,
    embedding_limit: int = 25,
) -> dict[str, Any]:
    """Draine un lot par profil sans retourner de contenu personnel."""

    profiles = [str(row["id"]) for row in list_user_profiles()]
    if not profiles:
        profiles = ["default"]
    report: dict[str, Any] = {"profiles": {}, "status": "ok"}
    for profile_id in profiles:
        try:
            with use_profile(profile_id):
                init_db()
                jobs = process_knowledge_jobs(limit=job_limit)
                backfill = backfill_knowledge(
                    batch_size=min(200, max(1, int(backfill_limit))),
                    max_items=max(1, int(backfill_limit)),
                    resume=True,
                )
                embeddings = process_knowledge_embeddings(limit=embedding_limit)
                pruned = prune_knowledge_jobs()
                metrics = get_knowledge_observability()
            report["profiles"][profile_id] = {
                "jobs": jobs,
                "backfill_status": backfill["status"],
                "backfill_indexed": backfill["indexed"],
                "embedding_status": embeddings["status"],
                "embeddings_indexed": embeddings["indexed"],
                "jobs_pruned": pruned,
                "jobs_by_status": metrics["jobs_by_status"],
                "pending_lag_seconds": metrics["pending_lag_seconds"],
                "index_lag_seconds": metrics["index_lag_seconds"],
            }
            if backfill["status"] != "ok" or embeddings["status"] == "degraded":
                report["status"] = "degraded"
        except Exception as exc:
            report["status"] = "degraded"
            report["profiles"][profile_id] = {"error_code": type(exc).__name__}
            logger.warning(
                "[retrieval-worker] maintenance profil indisponible: %s",
                type(exc).__name__,
            )
    return report


async def run_knowledge_worker(stop_event: asyncio.Event) -> None:
    """Boucle récurrente ; le premier lot démarre immédiatement après migration."""

    interval = max(
        5.0,
        float(getattr(config, "KNOWLEDGE_WORKER_INTERVAL_SECONDS", 60.0)),
    )
    while not stop_event.is_set():
        report = await asyncio.to_thread(run_knowledge_maintenance_once)
        logger.info(
            "[retrieval-worker] status=%s profils=%d",
            report["status"],
            len(report["profiles"]),
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue

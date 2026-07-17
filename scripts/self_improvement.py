"""Moteur d'auto-amélioration basé sur des preuves — propositions + PR Cursor."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

STATE_PATH = Path(config.BASE_DIR) / "data" / ".self_improvement_state.json"


def _load_state() -> dict:
    if not STATE_PATH.is_file():
        return {"proposals": []}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"proposals": []}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_evidence() -> list[dict[str, Any]]:
    """Observe latences, erreurs, jobs Cursor récurrents — zéro invention."""
    evidence: list[dict[str, Any]] = []
    try:
        from database.core import get_db

        with get_db() as conn:
            # Latences vocales élevées
            row = conn.execute(
                """
                SELECT AVG(latency_total_ms) AS avg_ms, COUNT(*) AS n
                FROM voice_debug_log
                WHERE created_at > datetime('now', '-7 days')
                """
            ).fetchone()
            if row and row["n"] and (row["avg_ms"] or 0) > 4000:
                evidence.append({
                    "type": "voice_latency",
                    "avg_ms": float(row["avg_ms"]),
                    "samples": int(row["n"]),
                    "impact": "Latence vocale moyenne élevée sur 7 jours",
                    "risk": "medium",
                    "template_id": "voice_pipeline",
                })
    except Exception as exc:
        logger.debug("[self_improvement] voice evidence: %s", exc)

    try:
        from database.cursor_jobs import list_cursor_jobs

        jobs = list_cursor_jobs(limit=30)
        failed = [j for j in jobs if j.get("status") == "failed"]
        if len(failed) >= 3:
            evidence.append({
                "type": "cursor_failures",
                "count": len(failed),
                "impact": "Échecs Cursor répétés — revue des prompts / tests",
                "risk": "medium",
                "template_id": "regression_review",
            })
    except Exception as exc:
        logger.debug("[self_improvement] cursor evidence: %s", exc)

    try:
        from database.core import get_db

        with get_db() as conn:
            # Actions LLM en échec répété (llm_action_logs) — preuve d'un outil
            # cassé ou d'un prompt qui produit des actions invalides.
            rows = conn.execute(
                """
                SELECT action_type, COUNT(*) AS n
                FROM llm_action_logs
                WHERE status = 'error'
                  AND created_at > datetime('now', '-7 days')
                GROUP BY action_type
                HAVING n >= 5
                ORDER BY n DESC
                LIMIT 3
                """
            ).fetchall()
            for row in rows:
                evidence.append({
                    "type": "action_failures",
                    "action_type": str(row["action_type"]),
                    "count": int(row["n"]),
                    "impact": f"Action `{row['action_type']}` en échec {row['n']} fois sur 7 jours",
                    "risk": "medium",
                    "template_id": "bug_fix",
                })
    except Exception as exc:
        logger.debug("[self_improvement] action evidence: %s", exc)

    try:
        from database.core import get_db

        with get_db() as conn:
            # Réponses vocales vides / erreurs pipeline — preuve de fragilité voix
            row = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM voice_debug_log
                WHERE created_at > datetime('now', '-7 days')
                  AND (response_clean = '' OR response_clean LIKE 'Desole%probleme technique%')
                """
            ).fetchone()
            if row and int(row["n"] or 0) >= 5:
                evidence.append({
                    "type": "voice_empty_responses",
                    "count": int(row["n"]),
                    "impact": f"{row['n']} tours vocaux sans réponse exploitable sur 7 jours",
                    "risk": "medium",
                    "template_id": "voice_pipeline",
                })
    except Exception as exc:
        logger.debug("[self_improvement] voice empty evidence: %s", exc)

    return evidence


async def propose_improvements(*, auto_delegate: bool = False) -> dict[str, Any]:
    """Produit des propositions ; optionnellement délègue à Cursor (PR only)."""
    if not getattr(config, "SELF_IMPROVEMENT_ENABLED", True):
        return {"ok": False, "error": "SELF_IMPROVEMENT_ENABLED=false", "proposals": []}

    evidence = collect_evidence()
    proposals: list[dict[str, Any]] = []
    for ev in evidence:
        proposals.append({
            "id": f"imp-{datetime.now().strftime('%Y%m%d%H%M%S')}-{ev['type']}",
            "evidence": ev,
            "expected_impact": ev.get("impact"),
            "risk_level": ev.get("risk", "medium"),
            "plan": f"Corriger la cause de: {ev.get('type')}",
            "template_id": ev.get("template_id", "self_improvement"),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "status": "proposed",
        })

    state = _load_state()
    state.setdefault("proposals", []).extend(proposals)
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    _save_state(state)

    jobs: list[dict[str, Any]] = []
    if auto_delegate and proposals and getattr(config, "CURSOR_DELEGATION_ENABLED", True):
        from integrations.cursor_delegation import cursor_delegation

        for p in proposals[:1]:  # une seule délégation par cycle
            job = await cursor_delegation.enqueue(
                title=f"Auto-amélioration: {p['evidence']['type']}",
                user_request=(
                    f"Preuve: {json.dumps(p['evidence'], ensure_ascii=False)}\n"
                    f"Plan: {p['plan']}\n"
                    "Implémente la correction minimale avec tests. Mode PR only."
                ),
                template_id=p["template_id"],
                risk_level=p["risk_level"],
                interaction_mode="scheduled",
                auto_start=True,
                require_confirmation=False,  # cycle scheduler opt-in via auto_delegate
            )
            p["status"] = "delegated"
            p["job_id"] = job.get("job_id")
            jobs.append(job)
        _save_state(state)

    return {"ok": True, "proposals": proposals, "jobs": jobs}


def list_proposals(limit: int = 20) -> list[dict[str, Any]]:
    state = _load_state()
    props = state.get("proposals") or []
    return list(reversed(props))[:limit]

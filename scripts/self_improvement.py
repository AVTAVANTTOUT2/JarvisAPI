"""Moteur d'auto-amélioration fondé sur des preuves et provider-neutral."""

from __future__ import annotations

import json
import hashlib
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


def _delegation_window(now: datetime | None = None) -> str:
    """Retourne la fenêtre stable qui rend un passage scheduler rejouable."""
    current = now or datetime.now()
    schedule = str(getattr(config, "SELF_IMPROVEMENT_SCHEDULE", "weekly")).casefold()
    if schedule == "daily":
        return current.strftime("%Y-%m-%d")
    iso = current.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _proposal_id(evidence: dict[str, Any], *, window: str) -> str:
    """Identifie une cause, indépendamment de ses compteurs volatils."""
    identity = {
        "type": evidence.get("type"),
        "action_type": evidence.get("action_type"),
        "template_id": evidence.get("template_id"),
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    return f"imp-{window}-{digest}"


def collect_evidence() -> list[dict[str, Any]]:
    """Observe latences, erreurs et runs agentiques récurrents — zéro invention."""
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
        from jarvis.agentic.models import AgenticRunStatus
        from jarvis.agentic.service import get_agentic_service

        failed_statuses = {
            AgenticRunStatus.FAILED,
            AgenticRunStatus.EXPIRED,
            AgenticRunStatus.PROVIDER_UNAVAILABLE,
        }
        runs = get_agentic_service().list(limit=30)
        failed = [run for run in runs if run.status in failed_statuses]
        if len(failed) >= 3:
            evidence.append({
                "type": "agentic_failures",
                "count": len(failed),
                "impact": "Échecs agentiques répétés — revue du contexte et des tests",
                "risk": "medium",
                "template_id": "regression_review",
            })
    except Exception as exc:
        logger.debug("[self_improvement] agentic evidence: %s", exc)

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
    """Produit des propositions ; optionnellement lance un workflow PR-only."""
    if not getattr(config, "SELF_IMPROVEMENT_ENABLED", True):
        return {"ok": False, "error": "SELF_IMPROVEMENT_ENABLED=false", "proposals": []}

    evidence = collect_evidence()
    state = _load_state()
    existing_by_id = {
        str(item.get("id")): item
        for item in state.get("proposals", [])
        if isinstance(item, dict) and item.get("id")
    }
    window = _delegation_window()
    proposals: list[dict[str, Any]] = []
    new_proposals: list[dict[str, Any]] = []
    delegation_candidates: list[dict[str, Any]] = []
    for ev in evidence:
        proposal_id = _proposal_id(ev, window=window)
        existing = existing_by_id.get(proposal_id)
        if existing is not None:
            existing["evidence"] = ev
            proposals.append(existing)
            if existing.get("status") != "delegated":
                delegation_candidates.append(existing)
            continue
        proposal = {
            "id": proposal_id,
            "evidence": ev,
            "expected_impact": ev.get("impact"),
            "risk_level": ev.get("risk", "medium"),
            "plan": f"Corriger la cause de: {ev.get('type')}",
            "template_id": ev.get("template_id", "self_improvement"),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "status": "proposed",
        }
        proposals.append(proposal)
        new_proposals.append(proposal)
        delegation_candidates.append(proposal)

    state.setdefault("proposals", []).extend(new_proposals)
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    _save_state(state)

    jobs: list[dict[str, Any]] = []
    if auto_delegate and delegation_candidates:
        from agents.devagent.agentic_runtime import delegate_engineering_task

        for p in delegation_candidates[:1]:  # une seule délégation par fenêtre
            job = await delegate_engineering_task(
                title=f"Auto-amélioration: {p['evidence']['type']}",
                user_request=(
                    f"Preuve: {json.dumps(p['evidence'], ensure_ascii=False)}\n"
                    f"Plan: {p['plan']}\n"
                    "Implémente la correction minimale avec un test de non-régression."
                ),
                template_id=p["template_id"],
                workflow_id="self_improvement",
                risk=p["risk_level"],
                interaction_mode="scheduled",
                origin="scheduler",
                channel="self_improvement",
                task_id=p["id"],
                idempotency_key=f"scheduler:self-improvement:{p['id']}",
                selected_context={
                    "delivery_owner": "jarvis",
                    "scheduler_window": window,
                    "expected_impact": p["expected_impact"],
                },
                evidence=p["evidence"],
                permissions=("workspace:read", "workspace:write"),
                acceptance_criteria=(
                    "La cause observée est corrigée par un changement minimal",
                    "Un test déterministe couvre la régression",
                    "Aucune opération Git, publication ou déploiement n'est exécutée par le runtime",
                ),
                required_tests=(
                    ("python", "-m", "pytest", "tests/", "-q", "--tb=line", "-x"),
                ),
                auto_start=True,
                require_confirmation=False,
                delivery_mode="pr_only",
                repo_root=Path(config.BASE_DIR),
                wait_for_completion=False,
            )
            p["status"] = "delegated"
            p["job_id"] = job.get("job_id")
            p["run_id"] = job.get("run_id")
            jobs.append(job)
        _save_state(state)

    return {"ok": True, "proposals": proposals, "jobs": jobs}


def list_proposals(limit: int = 20) -> list[dict[str, Any]]:
    state = _load_state()
    props = state.get("proposals") or []
    return list(reversed(props))[:limit]

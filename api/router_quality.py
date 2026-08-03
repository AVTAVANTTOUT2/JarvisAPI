"""Routes de qualité, sécurité et migrations techniques."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from core.network_security import is_loopback_request

router = APIRouter()


@router.post("/api/quality/ci/run")
async def api_quality_ci_run():
    """Déclenche la CI locale (lint + tests + build front optionnel) à la demande."""
    from scripts.local_ci import run_local_ci

    return await asyncio.to_thread(run_local_ci)


@router.post("/api/quality/ci/install-hook")
async def api_quality_ci_install_hook(request: Request, force: bool = False):
    """Installe localement notre hook sans pouvoir écraser un hook tiers."""
    if not is_loopback_request(request):
        raise HTTPException(403, "Installation du hook autorisée uniquement en local")
    if force:
        raise HTTPException(
            409,
            "L'écrasement d'un hook tiers est interdit via l'API ; utilisez la CLI locale",
        )

    from database import log_llm_action
    from scripts.install_git_hooks import install

    try:
        log_llm_action(
            "quality",
            "quality_hook_install",
            {"source": "local_api", "force": False},
            "pending",
        )
    except Exception as exc:
        raise HTTPException(503, "Journal d'audit qualité indisponible") from exc

    result = install(force=False)
    if not result.get("ok"):
        raise HTTPException(409, result.get("reason", "Installation du hook refusée."))
    return result


@router.get("/api/quality/duplicates")
async def api_quality_duplicates():
    """Blocs de code dupliqué détectés (scan périodique, rapport seul)."""
    from scripts.duplicate_scanner import list_open_duplicates

    return {"duplicates": list_open_duplicates()}


@router.post("/api/quality/duplicates/scan")
async def api_quality_duplicates_scan():
    """Déclenche un scan de code dupliqué immédiat sur la codebase JARVIS."""
    from scripts.duplicate_scanner import scan_and_report

    return await asyncio.to_thread(scan_and_report)



@router.get("/api/quality/security")
async def api_quality_security():
    """Constats de l'audit sécurité (secrets, patterns dangereux)."""
    from scripts.security_audit import list_open_findings

    return {"findings": list_open_findings()}


@router.post("/api/quality/security/scan")
async def api_quality_security_scan():
    """Déclenche un audit sécurité immédiat sur la codebase JARVIS."""
    from scripts.security_audit import scan_and_report

    return await asyncio.to_thread(scan_and_report)


@router.post("/api/quality/security/{finding_id}/fix")
async def api_quality_security_fix(finding_id: int):
    """Propose le correctif dans un worktree Cursor avec livraison PR-only."""
    from database import get_security_findings
    from integrations.cursor_delegation import CursorDelegationError
    from jarvis.security.redaction import public_cursor_job_view
    from scripts.quality_delegation import QualityDelegationError, delegate_security_fix

    finding = next(
        (
            item
            for item in get_security_findings("open", limit=1000)
            if item["id"] == finding_id
        ),
        None,
    )
    if not finding:
        raise HTTPException(404, "Constat introuvable ou déjà résolu.")
    try:
        job = await delegate_security_fix(
            finding,
            interaction_mode="chat",
            auto_start=False,
            require_confirmation=True,
        )
    except (QualityDelegationError, CursorDelegationError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "delegated": True, "job": public_cursor_job_view(job)}


@router.post("/api/quality/tests/generate")
async def api_quality_generate_tests():
    """Propose les tests manquants via Cursor, avec confirmation et PR obligatoire."""
    from integrations.cursor_delegation import CursorDelegationError
    from jarvis.security.redaction import public_cursor_job_view
    from scripts.quality_delegation import QualityDelegationError, delegate_missing_tests

    try:
        job = await delegate_missing_tests(
            interaction_mode="chat",
            auto_start=False,
            require_confirmation=True,
        )
    except (QualityDelegationError, CursorDelegationError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "delegated": True, "job": public_cursor_job_view(job)}


@router.get("/api/migrations/status")
async def api_migrations_status():
    """Migrations SQLite appliquées / en attente."""
    from scripts.db_migrations import migration_status

    return migration_status()


@router.post("/api/migrations/run")
async def api_migrations_run():
    """Applique les migrations en attente (sauvegarde automatique préalable)."""
    from scripts.db_migrations import apply_pending_migrations

    report = await asyncio.to_thread(apply_pending_migrations)
    if not report["ok"]:
        raise HTTPException(500, report["error"] or "Migration échouée")
    return report

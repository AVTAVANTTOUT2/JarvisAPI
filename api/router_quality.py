"""Routes de qualité, sécurité et migrations techniques."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.post("/api/quality/ci/run")
async def api_quality_ci_run():
    """Déclenche la CI locale (lint + tests + build front optionnel) à la demande."""
    from scripts.local_ci import run_local_ci

    return await asyncio.to_thread(run_local_ci)


@router.post("/api/quality/ci/install-hook")
async def api_quality_ci_install_hook(force: bool = False):
    """Installe le hook pre-commit qui déclenche la CI locale à chaque commit."""
    from scripts.install_git_hooks import install

    result = install(force=force)
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
    """Applique le correctif mécanique (redaction) — requiert SECURITY_AUTO_FIX_ENABLED."""
    from database import get_security_findings
    from scripts.security_audit import apply_safe_fix

    finding = next((f for f in get_security_findings("open", limit=1000) if f["id"] == finding_id), None)
    if not finding:
        raise HTTPException(404, "Constat introuvable ou déjà résolu.")
    return await asyncio.to_thread(apply_safe_fix, finding)


@router.post("/api/quality/tests/generate")
async def api_quality_generate_tests():
    """Génère des tests pour les fonctions non couvertes (opt-in, cf. .env.example)."""
    from scripts.test_coverage_scan import run_test_generation

    return await run_test_generation()


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

"""Délégation agentique PR-only des opérations qualité du dépôt."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import config


class QualityDelegationError(RuntimeError):
    """La mutation qualité ne peut pas être proposée sans violer la politique."""


def _delegation_key(
    workflow: str,
    identity: dict[str, Any],
    *,
    interaction_mode: str,
) -> str:
    """Construit une clé bornée, stable dans la fenêtre d'exécution."""
    now = datetime.now(timezone.utc)
    if interaction_mode == "scheduled":
        iso = now.isocalendar()
        window = f"{iso.year}-W{iso.week:02d}"
    else:
        window = now.strftime("%Y-%m-%d")
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"quality:{workflow}:{window}:{digest}"


def _configured_test_targets() -> list[str]:
    """Retourne uniquement des cibles relatives sûres pour le runtime."""
    targets: list[str] = []
    for raw in str(getattr(config, "AUTO_TEST_GEN_TARGET_DIRS", "")).split(","):
        value = raw.strip().replace("\\", "/")
        if not value:
            continue
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise QualityDelegationError(
                f"AUTO_TEST_GEN_TARGET_DIRS contient une cible interdite: {value}"
            )
        targets.append(path.as_posix())
    if not targets:
        raise QualityDelegationError(
            "AUTO_TEST_GEN_TARGET_DIRS vide — aucune cible configurée"
        )
    return targets


async def delegate_security_fix(
    finding: dict[str, Any],
    *,
    interaction_mode: str,
    auto_start: bool,
    require_confirmation: bool,
) -> dict[str, Any]:
    """Propose le correctif d'un secret via un run agentique isolé."""
    if not bool(getattr(config, "SECURITY_AUTO_FIX_ENABLED", False)):
        raise QualityDelegationError("SECURITY_AUTO_FIX_ENABLED désactivé")
    rule = str(finding.get("rule") or "")
    if not rule.startswith("secret_"):
        raise QualityDelegationError(
            "correctif réservé aux secrets ; les patterns dangereux exigent une revue"
        )

    file_name = str(finding.get("file") or "fichier inconnu")
    line = int(finding.get("line") or 0)
    identity = {"rule": rule, "file": file_name, "line": line}
    idempotency_key = _delegation_key(
        "security-fix",
        identity,
        interaction_mode=interaction_mode,
    )
    from agents.devagent.agentic_runtime import delegate_engineering_task

    return await delegate_engineering_task(
        title=f"Audit sécurité: {rule} dans {file_name}"[:200],
        user_request=(
            f"Le scanner de sécurité signale `{rule}` dans `{file_name}` à la "
            f"ligne {line}. Corrige la fuite sans inclure le secret dans le rapport, "
            "ajoute ou adapte un test de non-régression, puis livre uniquement une PR."
        ),
        template_id="security_audit",
        workflow_id="security_fix",
        risk="high",
        interaction_mode=interaction_mode,
        origin="scheduler" if interaction_mode == "scheduled" else "user",
        channel="security_audit" if interaction_mode == "scheduled" else "quality",
        task_id=f"security-fix:{idempotency_key.rsplit(':', 1)[-1]}",
        idempotency_key=idempotency_key,
        selected_context={
            "delivery_owner": "jarvis",
            "require_confirmation": require_confirmation,
        },
        evidence=identity,
        permissions=("workspace:read", "workspace:write"),
        acceptance_criteria=[
            "Le secret détecté n'est plus présent dans le fichier suivi",
            "Aucun secret ni contenu .env n'apparaît dans les logs ou la PR",
            "Les tests de sécurité pertinents passent",
        ],
        required_tests=[("python", "-m", "pytest", "tests/test_security_audit.py", "-q")],
        auto_start=auto_start or require_confirmation,
        require_confirmation=require_confirmation,
        delivery_mode="pr_only",
        repo_root=Path(config.BASE_DIR),
        wait_for_completion=False,
    )


async def delegate_missing_tests(
    *,
    interaction_mode: str,
    auto_start: bool,
    require_confirmation: bool,
) -> dict[str, Any]:
    """Délègue les tests à un run isolé sans écrire dans le checkout actif."""
    if not bool(getattr(config, "AUTO_TEST_GEN_ENABLED", False)):
        raise QualityDelegationError("AUTO_TEST_GEN_ENABLED désactivé")
    targets = _configured_test_targets()

    target_list = ", ".join(f"`{target}`" for target in targets)
    identity = {"targets": targets}
    idempotency_key = _delegation_key(
        "test-generation",
        identity,
        interaction_mode=interaction_mode,
    )
    from agents.devagent.agentic_runtime import delegate_engineering_task

    return await delegate_engineering_task(
        title="Créer les tests manquants",
        user_request=(
            f"Analyse les fonctions publiques insuffisamment testées dans {target_list}. "
            "Ajoute un lot minimal de tests déterministes, exécute-les et livre "
            "uniquement une PR ; ne modifie jamais le checkout principal."
        ),
        template_id="test_creation",
        workflow_id="test_generation",
        risk="medium",
        interaction_mode=interaction_mode,
        origin="scheduler" if interaction_mode == "scheduled" else "user",
        channel="test_generation" if interaction_mode == "scheduled" else "quality",
        task_id=f"test-generation:{idempotency_key.rsplit(':', 1)[-1]}",
        idempotency_key=idempotency_key,
        selected_context={
            "delivery_owner": "jarvis",
            "require_confirmation": require_confirmation,
            "targets": targets,
        },
        evidence=identity,
        permissions=("workspace:read", "workspace:write"),
        acceptance_criteria=[
            "Les nouveaux tests couvrent un comportement observable non couvert",
            "Les tests ajoutés et la suite pertinente passent",
            "Aucun fichier généré n'est écrit hors du worktree JARVIS",
        ],
        required_tests=[
            ("python", "-m", "pytest", "tests/", "-q", "--tb=line", "-x")
        ],
        auto_start=auto_start or require_confirmation,
        require_confirmation=require_confirmation,
        delivery_mode="pr_only",
        repo_root=Path(config.BASE_DIR),
        wait_for_completion=False,
    )

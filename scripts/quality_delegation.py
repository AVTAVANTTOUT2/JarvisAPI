"""Délégation PR-only des opérations qualité qui modifient le dépôt."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

import config


class QualityDelegationError(RuntimeError):
    """La mutation qualité ne peut pas être proposée sans violer la politique."""


def _configured_test_targets() -> list[str]:
    """Retourne uniquement des cibles relatives sûres pour le prompt Cursor."""
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
    """Propose le correctif d'un secret via un worktree et une PR Cursor."""
    if not bool(getattr(config, "SECURITY_AUTO_FIX_ENABLED", False)):
        raise QualityDelegationError("SECURITY_AUTO_FIX_ENABLED désactivé")
    rule = str(finding.get("rule") or "")
    if not rule.startswith("secret_"):
        raise QualityDelegationError(
            "correctif réservé aux secrets ; les patterns dangereux exigent une revue"
        )

    from integrations.cursor_delegation import cursor_delegation

    file_name = str(finding.get("file") or "fichier inconnu")
    line = int(finding.get("line") or 0)
    return await cursor_delegation.enqueue(
        title=f"Audit sécurité: {rule} dans {file_name}"[:200],
        user_request=(
            f"Le scanner de sécurité signale `{rule}` dans `{file_name}` à la "
            f"ligne {line}. Corrige la fuite sans inclure le secret dans le rapport, "
            "ajoute ou adapte un test de non-régression, puis livre uniquement une PR."
        ),
        template_id="security_audit",
        risk_level="high",
        interaction_mode=interaction_mode,
        acceptance_criteria=[
            "Le secret détecté n'est plus présent dans le fichier suivi",
            "Aucun secret ni contenu .env n'apparaît dans les logs ou la PR",
            "Les tests de sécurité pertinents passent",
        ],
        required_tests=["pytest tests/test_security_audit.py -q"],
        auto_start=auto_start,
        require_confirmation=require_confirmation,
        delivery_mode="pr_only",
    )


async def delegate_missing_tests(
    *,
    interaction_mode: str,
    auto_start: bool,
    require_confirmation: bool,
) -> dict[str, Any]:
    """Délègue la création de tests à Cursor sans écriture dans le checkout actif."""
    if not bool(getattr(config, "AUTO_TEST_GEN_ENABLED", False)):
        raise QualityDelegationError("AUTO_TEST_GEN_ENABLED désactivé")
    targets = _configured_test_targets()

    from integrations.cursor_delegation import cursor_delegation

    target_list = ", ".join(f"`{target}`" for target in targets)
    return await cursor_delegation.enqueue(
        title="Créer les tests manquants",
        user_request=(
            f"Analyse les fonctions publiques insuffisamment testées dans {target_list}. "
            "Ajoute un lot minimal de tests déterministes, exécute-les et livre "
            "uniquement une PR ; ne modifie jamais le checkout principal."
        ),
        template_id="test_creation",
        risk_level="medium",
        interaction_mode=interaction_mode,
        acceptance_criteria=[
            "Les nouveaux tests couvrent un comportement observable non couvert",
            "Les tests ajoutés et la suite pertinente passent",
            "Aucun fichier généré n'est écrit hors du worktree Cursor",
        ],
        required_tests=["pytest tests/ -q --tb=line -x"],
        auto_start=auto_start,
        require_confirmation=require_confirmation,
        delivery_mode="pr_only",
    )

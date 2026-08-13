"""Traduction des événements runtime en activité lisible et sûre.

L'interface veut montrer ce que font les agents — l'étape en cours, l'outil
appelé, le fichier touché, le test lancé. Elle ne doit jamais montrer le
raisonnement interne : ni prompt système, ni chaîne de pensée, ni sortie
d'outil brute.

La garantie repose sur une **allowlist de sortie**, pas sur un filtrage du
contenu. Chaque activité est reconstruite à partir de champs nommés, avec des
libellés écrits ici ; rien de ce que le runtime a produit ne traverse tel quel
sauf des identifiants et des noms d'outil déjà neutralisés en amont par
``jarvis.agentic.redaction``. Un runtime qui déciderait d'émettre sa réflexion
dans ``payload`` ne trouverait donc aucun champ où la faire passer.
"""

from __future__ import annotations

from typing import Any, Mapping

from jarvis.agentic.redaction import redact_text

from .models import (
    TaskActivity,
    TaskActivityLevel,
    TaskActivityType,
    clamp_text,
    new_id,
    utc_now,
)

#: Champs du payload runtime que l'activité a le droit de lire.
_READABLE_PAYLOAD_FIELDS = frozenset(
    {
        "action",
        "approval_id",
        "artifact_id",
        "decision",
        "error_code",
        "phase",
        "progress",
        "risks",
        "spoken_summary",
        "status",
        "title",
        "tool",
        "violation",
        "cancellation_kind",
    }
)

_PHASE_LABELS = {
    "planning": "Planification",
    "runtime_started": "Démarrage de l'exécution",
    "runtime_completed": "Exécution terminée",
    "runtime_failed": "Exécution en échec",
    "awaiting_jarvis_validation": "Attente des validations JARVIS",
    "verification": "Vérification",
}

#: `type d'événement runtime` → `(type d'activité, rôle d'agent, gabarit, niveau)`
_EVENT_MAP: dict[str, tuple[TaskActivityType, str, str, TaskActivityLevel]] = {
    "agent.run.started": (
        TaskActivityType.AGENT_STARTED,
        "executor",
        "Exécution démarrée.",
        TaskActivityLevel.SUMMARY,
    ),
    "agent.run.provisioning": (
        TaskActivityType.AGENT_SUMMARY,
        "executor",
        "Préparation de l'espace de travail.",
        TaskActivityLevel.DETAIL,
    ),
    "agent.run.phase_changed": (
        TaskActivityType.PLAN_STEP_STARTED,
        "executor",
        "Étape en cours.",
        TaskActivityLevel.DETAIL,
    ),
    "agent.run.awaiting_approval": (
        TaskActivityType.PERMISSION_REQUESTED,
        "executor",
        "Une autorisation est nécessaire pour continuer.",
        TaskActivityLevel.SUMMARY,
    ),
    "agent.run.paused": (
        TaskActivityType.AGENT_SUMMARY,
        "executor",
        "Exécution mise en pause.",
        TaskActivityLevel.SUMMARY,
    ),
    "agent.run.resumed": (
        TaskActivityType.AGENT_SUMMARY,
        "executor",
        "Exécution reprise.",
        TaskActivityLevel.SUMMARY,
    ),
    "agent.run.blocked": (
        TaskActivityType.BLOCKED,
        "executor",
        "Exécution bloquée.",
        TaskActivityLevel.SUMMARY,
    ),
    "agent.run.verifying": (
        TaskActivityType.REVIEW_STARTED,
        "reviewer",
        "Vérification des résultats.",
        TaskActivityLevel.SUMMARY,
    ),
    "agent.run.reviewing": (
        TaskActivityType.REVIEW_STARTED,
        "reviewer",
        "Revue des livrables par JARVIS.",
        TaskActivityLevel.SUMMARY,
    ),
    "agent.run.cancelling": (
        TaskActivityType.AGENT_SUMMARY,
        "executor",
        "Annulation demandée.",
        TaskActivityLevel.SUMMARY,
    ),
    "agent.run.cancelled": (
        TaskActivityType.COMPLETED,
        "executor",
        "Exécution annulée.",
        TaskActivityLevel.SUMMARY,
    ),
    "agent.run.completed": (
        TaskActivityType.COMPLETED,
        "executor",
        "Exécution terminée.",
        TaskActivityLevel.SUMMARY,
    ),
    "agent.run.failed": (
        TaskActivityType.ERROR,
        "executor",
        "Exécution en échec.",
        TaskActivityLevel.SUMMARY,
    ),
    "agent.run.expired": (
        TaskActivityType.ERROR,
        "executor",
        "Exécution expirée.",
        TaskActivityLevel.SUMMARY,
    ),
    "agent.run.provider_unavailable": (
        TaskActivityType.ERROR,
        "executor",
        "Le runtime d'exécution est indisponible.",
        TaskActivityLevel.SUMMARY,
    ),
    "agent.tool.started": (
        TaskActivityType.TOOL_STARTED,
        "executor",
        "Outil appelé.",
        TaskActivityLevel.DETAIL,
    ),
    "agent.tool.completed": (
        TaskActivityType.TOOL_COMPLETED,
        "executor",
        "Outil terminé.",
        TaskActivityLevel.DETAIL,
    ),
    "agent.tool.failed": (
        TaskActivityType.WARNING,
        "executor",
        "Outil en échec.",
        TaskActivityLevel.DETAIL,
    ),
    "agent.approval.requested": (
        TaskActivityType.PERMISSION_REQUESTED,
        "executor",
        "Autorisation demandée.",
        TaskActivityLevel.SUMMARY,
    ),
    "agent.approval.resolved": (
        TaskActivityType.PERMISSION_DECIDED,
        "user",
        "Autorisation tranchée.",
        TaskActivityLevel.SUMMARY,
    ),
    "agent.artifact.created": (
        TaskActivityType.FILE_CHANGED,
        "executor",
        "Livrable produit.",
        TaskActivityLevel.DETAIL,
    ),
}

#: Outils qui décrivent mieux la nature de l'activité que l'événement lui-même.
_TOOL_ACTIVITY_OVERRIDES: dict[str, tuple[TaskActivityType, str]] = {
    "read_file": (TaskActivityType.FILE_READ, "Lecture d'un fichier"),
    "list_files": (TaskActivityType.FILE_READ, "Inventaire de fichiers"),
    "search_code": (TaskActivityType.FILE_READ, "Recherche dans le code"),
    "write_file": (TaskActivityType.FILE_CHANGED, "Modification d'un fichier"),
    "edit": (TaskActivityType.FILE_CHANGED, "Modification d'un fichier"),
    "run_tests": (TaskActivityType.TEST_STARTED, "Exécution des tests"),
    "pytest": (TaskActivityType.TEST_STARTED, "Exécution des tests"),
    "git": (TaskActivityType.TOOL_STARTED, "Opération Git"),
}


def _safe_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = payload or {}
    return {key: source[key] for key in _READABLE_PAYLOAD_FIELDS if key in source}


def _tool_name(payload: Mapping[str, Any]) -> str:
    return clamp_text(payload.get("tool") or "", 120)


def build_activity(
    *,
    task_id: str,
    run_id: str | None,
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    sequence: int = 0,
) -> TaskActivity | None:
    """Traduit un événement runtime, ou ``None`` si rien de présentable.

    Retourner ``None`` est un résultat légitime : un événement interne au
    fournisseur n'a pas vocation à devenir une ligne dans l'écran de
    l'utilisateur.
    """

    mapping = _EVENT_MAP.get(event_type)
    if mapping is None:
        return None
    activity_type, role, template, level = mapping
    safe = _safe_payload(payload)
    tool = _tool_name(safe)
    summary = template

    if event_type == "agent.run.phase_changed":
        phase = clamp_text(safe.get("phase") or "", 120)
        label = _PHASE_LABELS.get(phase)
        if label is None and not phase:
            return None
        summary = label or f"Étape : {phase}"
        if phase == "runtime_completed":
            activity_type = TaskActivityType.PLAN_STEP_COMPLETED
    elif tool and event_type.startswith("agent.tool."):
        override = _TOOL_ACTIVITY_OVERRIDES.get(tool.lower())
        if override is not None:
            activity_type, label = override
            summary = (
                f"{label} — terminé"
                if event_type == "agent.tool.completed"
                else label
            )
            if event_type == "agent.tool.completed" and activity_type in {
                TaskActivityType.TEST_STARTED
            }:
                activity_type = TaskActivityType.TEST_RESULT
        else:
            summary = f"{template} ({tool})"
    elif event_type == "agent.approval.resolved":
        decision = clamp_text(safe.get("decision") or "", 40)
        summary = {
            "approved": "Autorisation accordée.",
            "denied": "Autorisation refusée.",
            "expired": "Autorisation expirée sans décision.",
        }.get(decision, template)
    elif event_type == "agent.artifact.created":
        activity_type = TaskActivityType.FILE_CHANGED
        summary = "Livrable produit."
    elif event_type in {"agent.run.failed", "agent.run.blocked"}:
        code = clamp_text(safe.get("error_code") or "", 60)
        violation = clamp_text(safe.get("violation") or "", 60)
        detail = " / ".join(part for part in (code, violation) if part)
        if detail:
            summary = f"{template} ({detail})"

    # `spoken_summary` est déjà une phrase produite par JARVIS pour la voix :
    # c'est la seule chaîne libre autorisée, et elle repasse par la redaction.
    spoken = redact_text(safe.get("spoken_summary") or "", max_chars=240)
    if spoken and spoken not in summary:
        summary = f"{summary} {spoken}".strip()

    return TaskActivity(
        activity_id=new_id("act"),
        task_id=task_id,
        run_id=run_id,
        sequence=sequence,
        event_type=activity_type,
        summary=summary,
        agent_id=clamp_text(role, 120),
        agent_role=role,
        phase=clamp_text(safe.get("phase") or "", 120),
        tool_name=tool,
        artifact_reference=clamp_text(safe.get("artifact_id") or "", 512),
        status=clamp_text(safe.get("status") or "", 60),
        level=level,
        created_at=utc_now(),
    )


def build_user_activity(
    *,
    task_id: str,
    summary: str,
    event_type: TaskActivityType = TaskActivityType.USER_COMMENT,
    run_id: str | None = None,
    level: TaskActivityLevel = TaskActivityLevel.SUMMARY,
) -> TaskActivity:
    """Journalise une intervention humaine dans le même flux que les agents."""

    return TaskActivity(
        activity_id=new_id("act"),
        task_id=task_id,
        run_id=run_id,
        sequence=0,
        event_type=event_type,
        summary=redact_text(summary, max_chars=500),
        agent_id="user",
        agent_role="user",
        level=level,
        created_at=utc_now(),
    )


__all__ = ["build_activity", "build_user_activity"]

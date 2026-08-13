"""Contexte lisible du pilotage de tâches, pour le chat et la voix.

JARVIS doit pouvoir répondre « où en est la tâche X ? », « qu'est-ce qui
demande mon attention ? », « qu'a fait l'agent ? », « où le travail a-t-il été
livré ? ». Ces réponses se construisent à partir de la base, pas d'un appel au
runtime : le moteur vocal ne parle jamais à un fournisseur d'exécution.

Le texte produit est **dense et borné**. Il ne contient ni raisonnement, ni
extrait de source, ni argument d'action : ce sont des états, des étapes et des
lieux de livraison. Le modèle qui le reçoit ne peut donc pas relayer à voix
haute ce que la frontière de redaction a déjà écarté.
"""

from __future__ import annotations

import logging
from typing import Any

from .models import (
    ATTENTION_TASK_STATUSES,
    EXECUTING_TASK_STATUSES,
    ControlTask,
    TaskStatus,
    clamp_text,
)

logger = logging.getLogger("jarvis")

MAX_LISTED_TASKS = 8
MAX_ACTIVITY_LINES = 6

#: Déclencheurs de la tranche de contexte. Volontairement étroits : « faire »
#: seul appartient déjà aux tâches simples, et élargir ici gonflerait le prompt
#: de tous les tours de parole.
CONTEXT_TRIGGERS: tuple[str, ...] = (
    "où en est",
    "ou en est",
    "avancement",
    "attention",
    "validation",
    "valider",
    "approuver",
    "plan",
    "autorisation",
    "permission",
    "livré",
    "livrée",
    "livrable",
    "rapport",
    "agent",
    "bloquée",
    "bloquee",
    "en cours",
)


def should_include_task_control(text: str) -> bool:
    lowered = str(text or "").casefold()
    return any(trigger in lowered for trigger in CONTEXT_TRIGGERS)


_STATUS_PHRASES: dict[TaskStatus, str] = {
    TaskStatus.AWAITING_PLAN_APPROVAL: "plan prêt, attend votre validation",
    TaskStatus.PLANNING: "plan en préparation",
    TaskStatus.APPROVED: "approuvée, démarrage imminent",
    TaskStatus.QUEUED: "en file d'attente",
    TaskStatus.RESOURCE_WAIT: "en attente de ressources",
    TaskStatus.RUNNING: "en cours d'exécution",
    TaskStatus.AWAITING_PERMISSION: "arrêtée sur une autorisation à donner",
    TaskStatus.VERIFYING: "en vérification",
    TaskStatus.BLOCKED: "bloquée",
    TaskStatus.CANCELLING: "en cours d'annulation",
    TaskStatus.COMPLETED: "terminée",
    TaskStatus.FAILED: "en échec",
    TaskStatus.CANCELLED: "annulée",
    TaskStatus.PLAN_REJECTED: "plan refusé",
    TaskStatus.PLAN_REVISION_REQUESTED: "en attente d'un nouveau plan",
}


def describe_status(task: ControlTask) -> str:
    return _STATUS_PHRASES.get(task.status, task.status.value)


def _line(task: ControlTask) -> str:
    parts = [f"« {clamp_text(task.title, 90)} » — {describe_status(task)}"]
    if task.status in EXECUTING_TASK_STATUSES and task.progress > 0:
        parts.append(f"{int(task.progress * 100)} %")
    if task.current_phase and task.status in EXECUTING_TASK_STATUSES:
        parts.append(task.current_phase)
    return " · ".join(parts)


def build_task_control_context(
    *, service: Any | None = None, focus_task_id: str | None = None
) -> dict[str, str]:
    """Assemble les tranches de contexte. Silencieux si rien n'est pertinent."""

    if service is None:
        from .service import get_task_control_service

        service = get_task_control_service()

    context: dict[str, str] = {}
    try:
        attention = service.list_tasks(
            statuses=sorted(ATTENTION_TASK_STATUSES, key=lambda s: s.value),
            limit=MAX_LISTED_TASKS,
        )
        active = service.list_tasks(
            statuses=sorted(EXECUTING_TASK_STATUSES, key=lambda s: s.value),
            limit=MAX_LISTED_TASKS,
        )
    except Exception:
        logger.warning("[ctx] pilotage de tâches indisponible")
        return context

    lines: list[str] = []
    if attention:
        lines.append("Attention requise :")
        lines.extend(f"- {_line(task)}" for task in attention[:MAX_LISTED_TASKS])
    running = [task for task in active if task.task_id not in {t.task_id for t in attention}]
    if running:
        lines.append("En cours :")
        lines.extend(f"- {_line(task)}" for task in running[:MAX_LISTED_TASKS])
    if not lines:
        lines.append("Aucune tâche en cours ni en attente de décision.")
    context["task_control_context"] = "\n".join(lines)

    if focus_task_id:
        detail = _focus(service, focus_task_id)
        if detail:
            context["task_control_focus"] = detail
    return context


def _focus(service: Any, task_id: str) -> str:
    """Détail d'une tâche : état, dernière activité, lieu de livraison."""

    try:
        task = service.repository.require_task(task_id)
    except Exception:
        return ""
    lines = [f"Tâche « {clamp_text(task.title, 90)} » : {describe_status(task)}."]
    if task.approved_plan_version:
        lines.append(f"Plan approuvé : version {task.approved_plan_version}.")
    try:
        entries = service.activity(task_id, limit=200)
    except Exception:
        entries = []
    if entries:
        lines.append("Dernières étapes :")
        lines.extend(
            f"- {clamp_text(entry.summary, 120)}"
            for entry in entries[-MAX_ACTIVITY_LINES:]
        )
    try:
        report = service.repository.latest_report(task_id)
    except Exception:
        report = None
    if report is not None:
        lines.append(f"Conclusion : {clamp_text(report.summary, 200)}")
        deliveries = report.data.get("deliveries") or []
        if deliveries:
            lines.append("Livré dans :")
            lines.extend(
                f"- {item.get('type', 'artefact')} : {clamp_text(item.get('reference', ''), 160)}"
                for item in list(deliveries)[:5]
            )
    return "\n".join(lines)


def find_task_by_title(title_fragment: str, *, service: Any | None = None) -> str | None:
    """Résout « la tâche rapport » vers un identifiant, sans deviner à l'aveugle.

    Retourne `None` dès que plusieurs tâches correspondent : mieux vaut
    demander de préciser que parler de la mauvaise tâche.
    """

    needle = " ".join(str(title_fragment or "").split()).casefold()
    if len(needle) < 3:
        return None
    if service is None:
        from .service import get_task_control_service

        service = get_task_control_service()
    try:
        tasks = service.list_tasks(limit=200)
    except Exception:
        return None
    matches = [task for task in tasks if needle in task.title.casefold()]
    return matches[0].task_id if len(matches) == 1 else None


__all__ = [
    "CONTEXT_TRIGGERS",
    "build_task_control_context",
    "describe_status",
    "find_task_by_title",
    "should_include_task_control",
]

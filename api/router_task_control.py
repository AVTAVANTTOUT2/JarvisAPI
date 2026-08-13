"""Routes du pilotage de tâches — `/api/task-control/*`.

Pourquoi une ressource distincte de `/api/tasks` : la ressource historique a
une identité entière et un modèle à trois états (`todo`/`doing`/`done`), et
elle est consommée par le bureau, le mobile et l'application macOS. La
surcharger imposerait soit de casser ces clients, soit une négociation de
version — pour rien, puisqu'une tâche pilotée a une identité opaque, un plan,
des approbations et un rapport. Les deux ressources coexistent, et la
migration relie chaque tâche historique à son miroir piloté.

Le client ne peut pas déclarer un état, une permission ni un digest : ces
valeurs sont posées par le service. Toutes les mutations passent par le verrou
de session et le jeton synchronisé du middleware — aucune exception n'est
ajoutée ici.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Query, Request

from api.errors import api_error, internal_error
from api.task_control_models import (
    ApprovalDecisionRequest,
    CandidateDecisionRequest,
    PlanDecisionRequest,
    TaskCancelRequest,
    TaskCommentRequest,
    TaskControlCreateRequest,
    TaskControlPatchRequest,
    TaskPlanRequest,
)
from database.task_control import (
    PlanNotFound,
    TaskNotFound,
    TaskPersistenceConflict,
)
from jarvis.task_control.models import (
    CandidateDecision,
    InvalidTaskTransition,
    PlanDecision,
    TaskActivityLevel,
    TaskExecutionRefused,
    TaskPriority,
    TaskSource,
    TaskSourceChannel,
    TaskSourceType,
    TaskStatus,
)
from jarvis.task_control.service import get_task_control_service

router = APIRouter(prefix="/api", tags=["task-control"])
logger = logging.getLogger("jarvis")

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

#: Regroupements de la barre latérale. Les noms sont ceux de l'interface, pas
#: ceux de la machine à états : « à valider » recouvre volontairement plusieurs
#: états, et la traduction vit ici pour que le client n'ait pas à la refaire.
_SECTIONS: dict[str, tuple[TaskStatus, ...]] = {
    "to_approve": (TaskStatus.AWAITING_PLAN_APPROVAL,),
    "planned": (
        TaskStatus.CREATED,
        TaskStatus.PLANNING,
        TaskStatus.APPROVED,
        TaskStatus.PLAN_REVISION_REQUESTED,
    ),
    "running": (
        TaskStatus.QUEUED,
        TaskStatus.RESOURCE_WAIT,
        TaskStatus.RUNNING,
        TaskStatus.VERIFYING,
        TaskStatus.CANCELLING,
    ),
    "attention": (
        TaskStatus.AWAITING_PLAN_APPROVAL,
        TaskStatus.AWAITING_PERMISSION,
        TaskStatus.BLOCKED,
    ),
    "completed": (TaskStatus.COMPLETED,),
    "failed": (TaskStatus.BLOCKED, TaskStatus.FAILED, TaskStatus.PLAN_REJECTED),
    "archived": (TaskStatus.ARCHIVED, TaskStatus.CANCELLED),
}


def _require_id(value: str, *, label: str) -> str:
    if not _ID_RE.fullmatch(value or ""):
        raise api_error(400, "invalid_identifier", f"{label} invalide")
    return value


def _actor(request: Request) -> str:
    session = getattr(request.state, "session", None)
    if isinstance(session, dict) and session.get("id"):
        return f"session:{session['id']}"
    device = getattr(request.state, "mobile_device", None)
    if isinstance(device, dict) and device.get("device_id"):
        return f"device:{device['device_id']}"
    return "authenticated-user"


def _translate(exc: Exception):
    """Traduit le domaine en erreurs publiques stables, sans fuite interne."""

    if isinstance(exc, TaskNotFound):
        return api_error(404, "task_not_found", "Tâche introuvable")
    if isinstance(exc, PlanNotFound):
        return api_error(404, "task_plan_not_found", "Version de plan introuvable")
    if isinstance(exc, TaskExecutionRefused):
        return api_error(
            409,
            "task_execution_refused",
            "Cette tâche n'a pas de plan approuvé exécutable",
        )
    if isinstance(exc, InvalidTaskTransition):
        return api_error(409, "invalid_task_transition", "Transition impossible")
    if isinstance(exc, TaskPersistenceConflict):
        return api_error(409, "task_conflict", "L'état de la tâche a changé")
    if isinstance(exc, ValueError):
        return api_error(400, "invalid_request", "Requête invalide")
    return None


def _fail(exc: Exception, code: str, message: str):
    translated = _translate(exc)
    if translated is not None:
        return translated
    logger.exception("%s : %s", code, type(exc).__name__)
    return internal_error(code, message)


# ── Tâches ────────────────────────────────────────────────────────────────


@router.get("/task-control/tasks")
async def list_control_tasks(
    section: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Liste par section d'interface, avec les compteurs de la barre latérale."""

    service = get_task_control_service()
    if section is not None and section not in _SECTIONS:
        raise api_error(400, "invalid_section", "Section inconnue")
    statuses = _SECTIONS.get(section) if section else None
    tasks = service.list_tasks(
        statuses=statuses,
        attention_only=section == "attention",
        limit=limit,
        offset=offset,
    )
    counts = service.repository.count_by_status()
    return {
        "tasks": [task.to_dict() for task in tasks],
        "counts": {
            name: sum(counts.get(status.value, 0) for status in members)
            for name, members in _SECTIONS.items()
        },
        "status_counts": counts,
    }


@router.post("/task-control/tasks", status_code=201)
async def create_control_task(
    payload: TaskControlCreateRequest, request: Request
) -> dict[str, Any]:
    """Crée une tâche et lance sa planification. Ne démarre jamais l'exécution."""

    service = get_task_control_service()
    source = TaskSource(
        source_type=TaskSourceType(payload.source_type),
        channel=TaskSourceChannel(payload.source_channel),
        reference=f"manual:{_actor(request)}",
    )
    try:
        task = await service.create_task(
            title=payload.title,
            description=payload.description,
            priority=TaskPriority(payload.priority),
            source=source,
            project_id=payload.project_id,
            conversation_id=payload.conversation_id,
            due_at=payload.due_at,
            autoplan=payload.autoplan,
        )
        if payload.comment:
            await service.add_comment(
                task.task_id, payload.comment, author=_actor(request)
            )
    except Exception as exc:
        raise _fail(exc, "task_create_failed", "Création de la tâche impossible") from exc
    return {"task": task.to_dict()}


@router.get("/task-control/tasks/{task_id}")
async def get_control_task(task_id: str) -> dict[str, Any]:
    """Détail complet : tâche, plans, dernier rapport, commentaires."""

    _require_id(task_id, label="task_id")
    service = get_task_control_service()
    try:
        task = service.repository.require_task(task_id)
    except Exception as exc:
        raise _fail(exc, "task_read_failed", "Lecture de la tâche impossible") from exc
    plans = service.repository.list_plans(task_id)
    report = service.repository.latest_report(task_id)
    return {
        "task": task.to_dict(),
        "plans": [plan.to_dict() for plan in plans],
        "current_plan": next(
            (plan.to_dict() for plan in plans if plan.version == task.plan_version),
            None,
        ),
        "report": report.to_dict() if report is not None else None,
        "comments": service.repository.list_comments(task_id),
    }


@router.patch("/task-control/tasks/{task_id}")
async def patch_control_task(
    task_id: str, payload: TaskControlPatchRequest
) -> dict[str, Any]:
    _require_id(task_id, label="task_id")
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        raise api_error(400, "empty_patch", "Aucun champ à modifier")
    service = get_task_control_service()
    try:
        task = service.repository.update_task(task_id, **fields)
    except Exception as exc:
        raise _fail(exc, "task_update_failed", "Mise à jour impossible") from exc
    return {"task": task.to_dict()}


@router.post("/task-control/tasks/{task_id}/cancel")
async def cancel_control_task(
    task_id: str, payload: TaskCancelRequest
) -> dict[str, Any]:
    _require_id(task_id, label="task_id")
    service = get_task_control_service()
    try:
        task = await service.cancel_task(task_id, reason=payload.reason)
    except Exception as exc:
        raise _fail(exc, "task_cancel_failed", "Annulation impossible") from exc
    return {"task": task.to_dict()}


# ── Plans ─────────────────────────────────────────────────────────────────


@router.post("/task-control/tasks/{task_id}/plan")
async def plan_control_task(task_id: str, payload: TaskPlanRequest) -> dict[str, Any]:
    """Produit une nouvelle version de plan, en lecture seule."""

    _require_id(task_id, label="task_id")
    service = get_task_control_service()
    try:
        task = await service.plan_task(task_id, revision_comment=payload.comment)
    except Exception as exc:
        raise _fail(exc, "task_plan_failed", "Planification impossible") from exc
    plan = (
        service.repository.get_plan(task_id, task.plan_version)
        if task.plan_version
        else None
    )
    return {"task": task.to_dict(), "plan": plan.to_dict() if plan else None}


@router.get("/task-control/tasks/{task_id}/plans")
async def list_control_plans(task_id: str) -> dict[str, Any]:
    _require_id(task_id, label="task_id")
    service = get_task_control_service()
    try:
        service.repository.require_task(task_id)
    except Exception as exc:
        raise _fail(exc, "task_read_failed", "Lecture impossible") from exc
    return {"plans": [plan.to_dict() for plan in service.repository.list_plans(task_id)]}


@router.post("/task-control/tasks/{task_id}/plans/{version}/decision")
async def decide_control_plan(
    task_id: str,
    version: int,
    payload: PlanDecisionRequest,
    request: Request,
) -> dict[str, Any]:
    """Accepte, refuse ou renvoie en révision une version de plan.

    Si le client fournit le digest affiché à l'écran, il doit correspondre :
    approuver un plan que l'utilisateur n'a pas sous les yeux serait
    exactement la faille que ce parcours existe pour fermer.
    """

    _require_id(task_id, label="task_id")
    if version < 1:
        raise api_error(400, "invalid_plan_version", "Version de plan invalide")
    service = get_task_control_service()
    if payload.plan_digest:
        plan = service.repository.get_plan(task_id, version)
        if plan is None:
            raise api_error(404, "task_plan_not_found", "Version de plan introuvable")
        if plan.digest != payload.plan_digest:
            raise api_error(
                409,
                "plan_digest_mismatch",
                "Le plan affiché n'est plus le plan courant ; rechargez-le",
            )
    try:
        task = await service.decide_plan(
            task_id,
            version,
            decision=PlanDecision(payload.decision),
            actor=_actor(request),
            comment=payload.comment,
        )
    except Exception as exc:
        raise _fail(exc, "plan_decision_failed", "Décision impossible") from exc
    return {"task": task.to_dict()}


# ── Activité, autorisations, résultat ─────────────────────────────────────


@router.get("/task-control/tasks/{task_id}/activity")
async def get_control_activity(
    task_id: str,
    after_sequence: int = Query(default=0, ge=0),
    level: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
) -> dict[str, Any]:
    """Activité ordonnée. `after_sequence` sert la reprise sans doublon."""

    _require_id(task_id, label="task_id")
    levels: list[TaskActivityLevel] | None = None
    if level is not None:
        try:
            wanted = TaskActivityLevel(level)
        except ValueError as exc:
            raise api_error(400, "invalid_level", "Niveau inconnu") from exc
        # Un niveau demandé inclut les niveaux plus synthétiques : demander
        # « technique » sans voir le résumé donnerait une lecture illisible.
        order = [
            TaskActivityLevel.SUMMARY,
            TaskActivityLevel.DETAIL,
            TaskActivityLevel.TECHNICAL,
        ]
        levels = order[: order.index(wanted) + 1]
    service = get_task_control_service()
    try:
        service.repository.require_task(task_id)
        entries = service.activity(
            task_id, after_sequence=after_sequence, levels=levels, limit=limit
        )
    except Exception as exc:
        raise _fail(exc, "task_activity_failed", "Lecture de l'activité impossible") from exc
    return {
        "activity": [entry.to_dict() for entry in entries],
        "last_sequence": entries[-1].sequence if entries else after_sequence,
    }


@router.get("/task-control/tasks/{task_id}/approvals")
async def list_control_approvals(task_id: str) -> dict[str, Any]:
    _require_id(task_id, label="task_id")
    service = get_task_control_service()
    try:
        approvals = service.pending_approvals(task_id)
    except Exception as exc:
        raise _fail(exc, "task_approvals_failed", "Lecture impossible") from exc
    return {"approvals": approvals}


@router.post("/task-control/tasks/{task_id}/approvals/{approval_id}/decision")
async def decide_control_approval(
    task_id: str,
    approval_id: str,
    payload: ApprovalDecisionRequest,
    request: Request,
) -> dict[str, Any]:
    """Autorise ou refuse **un** effet précis, une seule fois."""

    _require_id(task_id, label="task_id")
    _require_id(approval_id, label="approval_id")
    service = get_task_control_service()
    try:
        result = await service.decide_effect_approval(
            task_id,
            approval_id,
            approved=payload.decision == "approved",
            actor=_actor(request),
        )
    except Exception as exc:
        raise _fail(exc, "approval_decision_failed", "Décision impossible") from exc
    return result


@router.get("/task-control/tasks/{task_id}/report")
async def get_control_report(task_id: str) -> dict[str, Any]:
    _require_id(task_id, label="task_id")
    service = get_task_control_service()
    try:
        service.repository.require_task(task_id)
    except Exception as exc:
        raise _fail(exc, "task_read_failed", "Lecture impossible") from exc
    report = service.repository.latest_report(task_id)
    if report is None:
        raise api_error(404, "task_report_not_found", "Aucun rapport disponible")
    return {"report": report.to_dict()}


@router.get("/task-control/tasks/{task_id}/artifacts")
async def get_control_artifacts(task_id: str) -> dict[str, Any]:
    _require_id(task_id, label="task_id")
    service = get_task_control_service()
    try:
        artifacts = service.artifacts(task_id)
    except Exception as exc:
        raise _fail(exc, "task_artifacts_failed", "Lecture impossible") from exc
    return {"artifacts": artifacts}


@router.post("/task-control/tasks/{task_id}/comments", status_code=201)
async def add_control_comment(
    task_id: str, payload: TaskCommentRequest, request: Request
) -> dict[str, Any]:
    """Ajoute une précision, et seulement si demandé, relance la planification."""

    _require_id(task_id, label="task_id")
    service = get_task_control_service()
    try:
        comment = await service.add_comment(
            task_id, payload.body, author=_actor(request)
        )
        task = (
            await service.request_plan_revision(task_id, payload.body)
            if payload.request_plan_revision
            else service.repository.require_task(task_id)
        )
    except Exception as exc:
        raise _fail(exc, "task_comment_failed", "Commentaire impossible") from exc
    return {"comment": comment, "task": task.to_dict()}


# ── Candidats ─────────────────────────────────────────────────────────────


@router.get("/task-candidates")
async def list_task_candidates(
    decision: str | None = Query(default="pending"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    service = get_task_control_service()
    wanted: CandidateDecision | None = None
    if decision not in (None, "all"):
        try:
            wanted = CandidateDecision(decision)
        except ValueError as exc:
            raise api_error(400, "invalid_decision", "Décision inconnue") from exc
    candidates = service.repository.list_candidates(decision=wanted, limit=limit)
    return {"candidates": [item.to_dict() for item in candidates]}


@router.post("/task-candidates/{candidate_id}/decision")
async def decide_task_candidate(
    candidate_id: str, payload: CandidateDecisionRequest
) -> dict[str, Any]:
    """Accepte (→ tâche en attente de plan) ou écarte un candidat."""

    _require_id(candidate_id, label="candidate_id")
    service = get_task_control_service()
    try:
        if payload.decision == "accepted":
            task = await service.accept_candidate(candidate_id)
            candidate = service.repository.get_candidate(candidate_id)
            return {
                "candidate": candidate.to_dict() if candidate else None,
                "task": task.to_dict(),
            }
        candidate = service.decide_candidate(
            candidate_id,
            decision=CandidateDecision(payload.decision),
            merge_into=payload.merge_into,
        )
    except Exception as exc:
        raise _fail(exc, "candidate_decision_failed", "Décision impossible") from exc
    return {"candidate": candidate.to_dict(), "task": None}


__all__ = ["router"]

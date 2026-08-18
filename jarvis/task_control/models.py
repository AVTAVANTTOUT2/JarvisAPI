"""Domaine générique du pilotage de tâches — vocabulaire, états, invariants.

Ce module ne connaît ni fournisseur d'exécution, ni transport, ni base. Il
porte l'invariant central du produit :

    Aucune tâche, créée automatiquement ou manuellement, ne peut être exécutée
    avant validation explicite de la version de plan qui sera exécutée.

La garantie n'est pas déclarative : ``ensure_executable`` est le seul chemin
autorisé vers l'exécution et exige simultanément un plan approuvé, le digest
exact de la version approuvée, et un état de tâche compatible. Le client ne
peut pas la contourner puisqu'il ne fabrique jamais lui-même une transition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence
import uuid

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

MAX_TITLE_CHARS = 300
MAX_DESCRIPTION_CHARS = 8_000
MAX_EXCERPT_CHARS = 400
MAX_SUMMARY_CHARS = 500
MAX_STEPS = 40


def utc_now() -> datetime:
    """Horodatage UTC conscient du fuseau — jamais d'heure locale persistée."""

    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _frozen(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


def validate_identifier(value: str, *, label: str) -> str:
    candidate = str(value or "").strip()
    if not _SLUG_RE.fullmatch(candidate):
        raise ValueError(f"{label} invalide")
    return candidate


def clamp_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


# ──────────────────────────────────────────────────────────────────────────
# États
# ──────────────────────────────────────────────────────────────────────────


class TaskStatus(str, Enum):
    """États canoniques d'une tâche pilotée."""

    CANDIDATE = "candidate"
    CREATED = "created"
    PLANNING = "planning"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    PLAN_REJECTED = "plan_rejected"
    PLAN_REVISION_REQUESTED = "plan_revision_requested"
    APPROVED = "approved"
    QUEUED = "queued"
    RESOURCE_WAIT = "resource_wait"
    RUNNING = "running"
    AWAITING_PERMISSION = "awaiting_permission"
    CANCELLING = "cancelling"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


TERMINAL_TASK_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.ARCHIVED, TaskStatus.CANCELLED}
)

#: États dans lesquels un runtime d'exécution peut légitimement être actif.
EXECUTING_TASK_STATUSES: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.QUEUED,
        TaskStatus.RESOURCE_WAIT,
        TaskStatus.RUNNING,
        TaskStatus.AWAITING_PERMISSION,
        TaskStatus.VERIFYING,
        TaskStatus.CANCELLING,
    }
)

#: États dans lesquels la tâche attend une décision humaine.
ATTENTION_TASK_STATUSES: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.AWAITING_PLAN_APPROVAL,
        TaskStatus.AWAITING_PERMISSION,
        TaskStatus.BLOCKED,
    }
)

ALLOWED_TASK_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = (
    MappingProxyType(
        {
            TaskStatus.CANDIDATE: frozenset(
                {TaskStatus.CREATED, TaskStatus.ARCHIVED, TaskStatus.CANCELLED}
            ),
            TaskStatus.CREATED: frozenset(
                {
                    TaskStatus.PLANNING,
                    TaskStatus.CANCELLED,
                    TaskStatus.ARCHIVED,
                    TaskStatus.FAILED,
                }
            ),
            TaskStatus.PLANNING: frozenset(
                {
                    TaskStatus.AWAITING_PLAN_APPROVAL,
                    TaskStatus.BLOCKED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                }
            ),
            TaskStatus.AWAITING_PLAN_APPROVAL: frozenset(
                {
                    TaskStatus.APPROVED,
                    TaskStatus.PLAN_REJECTED,
                    TaskStatus.PLAN_REVISION_REQUESTED,
                    TaskStatus.CANCELLED,
                    TaskStatus.ARCHIVED,
                }
            ),
            TaskStatus.PLAN_REJECTED: frozenset(
                {
                    TaskStatus.PLANNING,
                    TaskStatus.ARCHIVED,
                    TaskStatus.CANCELLED,
                }
            ),
            TaskStatus.PLAN_REVISION_REQUESTED: frozenset(
                {
                    TaskStatus.PLANNING,
                    TaskStatus.ARCHIVED,
                    TaskStatus.CANCELLED,
                }
            ),
            TaskStatus.APPROVED: frozenset(
                {
                    TaskStatus.QUEUED,
                    TaskStatus.CANCELLED,
                    TaskStatus.FAILED,
                }
            ),
            # Le run est la source de vérité de son avancement, et ses
            # événements peuvent arriver hors séquence ou être rejoués. Une
            # tâche en file dont le run annonce une autorisation, une
            # vérification ou une fin doit pouvoir suivre : refuser l'arête
            # laisserait l'écran bloqué sur « en file » pour toujours — la
            # panne même que ces états servent à éviter.
            TaskStatus.QUEUED: frozenset(
                {
                    TaskStatus.RESOURCE_WAIT,
                    TaskStatus.RUNNING,
                    TaskStatus.AWAITING_PERMISSION,
                    TaskStatus.VERIFYING,
                    TaskStatus.COMPLETED,
                    TaskStatus.CANCELLING,
                    TaskStatus.BLOCKED,
                    TaskStatus.FAILED,
                }
            ),
            TaskStatus.RESOURCE_WAIT: frozenset(
                {
                    TaskStatus.QUEUED,
                    TaskStatus.RUNNING,
                    TaskStatus.AWAITING_PERMISSION,
                    TaskStatus.VERIFYING,
                    TaskStatus.COMPLETED,
                    TaskStatus.CANCELLING,
                    TaskStatus.BLOCKED,
                    TaskStatus.FAILED,
                }
            ),
            TaskStatus.RUNNING: frozenset(
                {
                    TaskStatus.AWAITING_PERMISSION,
                    TaskStatus.VERIFYING,
                    TaskStatus.BLOCKED,
                    TaskStatus.CANCELLING,
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                }
            ),
            TaskStatus.AWAITING_PERMISSION: frozenset(
                {
                    TaskStatus.RUNNING,
                    TaskStatus.VERIFYING,
                    TaskStatus.BLOCKED,
                    TaskStatus.CANCELLING,
                    TaskStatus.FAILED,
                }
            ),
            # `plan_revision_requested` est atteignable depuis `cancelling` :
            # arrêter un run pour réviser le périmètre n'est pas annuler la
            # tâche. Sans cette arête, la révision d'une tâche en cours
            # devrait passer par `cancelled`, qui est un verdict rendu à
            # l'utilisateur et non une étape de travail.
            TaskStatus.CANCELLING: frozenset(
                {
                    TaskStatus.CANCELLED,
                    TaskStatus.FAILED,
                    TaskStatus.COMPLETED,
                    TaskStatus.BLOCKED,
                    TaskStatus.PLAN_REVISION_REQUESTED,
                }
            ),
            TaskStatus.VERIFYING: frozenset(
                {
                    TaskStatus.COMPLETED,
                    TaskStatus.BLOCKED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLING,
                }
            ),
            TaskStatus.COMPLETED: frozenset({TaskStatus.ARCHIVED}),
            TaskStatus.BLOCKED: frozenset(
                {
                    TaskStatus.RUNNING,
                    TaskStatus.PLAN_REVISION_REQUESTED,
                    TaskStatus.CANCELLING,
                    TaskStatus.CANCELLED,
                    TaskStatus.FAILED,
                    TaskStatus.ARCHIVED,
                }
            ),
            TaskStatus.FAILED: frozenset(
                {
                    TaskStatus.PLAN_REVISION_REQUESTED,
                    TaskStatus.ARCHIVED,
                    TaskStatus.CANCELLED,
                }
            ),
            TaskStatus.CANCELLED: frozenset({TaskStatus.ARCHIVED}),
            TaskStatus.ARCHIVED: frozenset(),
        }
    )
)


class InvalidTaskTransition(ValueError):
    """Transition refusée par la machine à états canonique."""


class TaskExecutionRefused(PermissionError):
    """Tentative de démarrage sans plan approuvé, ou avec un plan divergent."""


def validate_task_transition(
    current: TaskStatus | str,
    target: TaskStatus | str,
) -> tuple[TaskStatus, TaskStatus]:
    current_status = TaskStatus(current)
    target_status = TaskStatus(target)
    if current_status is target_status:
        return current_status, target_status
    if target_status not in ALLOWED_TASK_TRANSITIONS[current_status]:
        raise InvalidTaskTransition(
            f"transition interdite: {current_status.value} -> {target_status.value}"
        )
    return current_status, target_status


# ──────────────────────────────────────────────────────────────────────────
# Vocabulaire annexe
# ──────────────────────────────────────────────────────────────────────────


class TaskSourceType(str, Enum):
    MANUAL = "manual"
    USER_REQUEST = "user_request"
    MESSAGE = "message"
    EMAIL = "email"
    SCHEDULER = "scheduler"


class TaskSourceChannel(str, Enum):
    MACOS = "macos"
    WEB = "web"
    VOICE = "voice"
    MOBILE = "mobile"
    IMESSAGE = "imessage"
    EMAIL = "email"
    API = "api"
    SCHEDULER = "scheduler"


class TaskPriority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PlanDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"
    SUPERSEDED = "superseded"


class ApprovalKind(str, Enum):
    """Deux pouvoirs distincts, jamais interchangeables.

    ``PLAN_APPROVAL`` autorise le *démarrage* d'une version de plan.
    ``EFFECT_APPROVAL`` autorise *un* effet précis, une seule fois, avec ses
    arguments exacts. Approuver un plan n'autorise aucun effet externe, et
    approuver un effet ne relance jamais une tâche.
    """

    PLAN_APPROVAL = "plan_approval"
    EFFECT_APPROVAL = "effect_approval"


class TaskActivityType(str, Enum):
    AGENT_STARTED = "agent_started"
    AGENT_SUMMARY = "agent_summary"
    DECISION_SUMMARY = "decision_summary"
    PLAN_STEP_STARTED = "plan_step_started"
    PLAN_STEP_COMPLETED = "plan_step_completed"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    FILE_READ = "file_read"
    FILE_CHANGED = "file_changed"
    TEST_STARTED = "test_started"
    TEST_RESULT = "test_result"
    REVIEW_STARTED = "review_started"
    REVIEW_RESULT = "review_result"
    PERMISSION_REQUESTED = "permission_requested"
    PERMISSION_DECIDED = "permission_decided"
    USER_COMMENT = "user_comment"
    WARNING = "warning"
    BLOCKED = "blocked"
    ERROR = "error"
    COMPLETED = "completed"


class TaskActivityLevel(str, Enum):
    """Niveaux d'affichage — l'interface ne noie pas l'utilisateur par défaut."""

    SUMMARY = "summary"
    DETAIL = "detail"
    TECHNICAL = "technical"


class CandidateDecision(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    IGNORED = "ignored"
    REJECTED = "rejected"
    FALSE_POSITIVE = "false_positive"
    MERGED = "merged"


# ──────────────────────────────────────────────────────────────────────────
# Structures
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlanStep:
    """Une étape lisible. Jamais un raisonnement brut, jamais un prompt."""

    index: int
    title: str
    detail: str = ""
    expected_result: str = ""
    tools: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.index < 1:
            raise ValueError("index d'étape >= 1")
        object.__setattr__(self, "title", clamp_text(self.title, 200))
        if not self.title:
            raise ValueError("titre d'étape requis")
        object.__setattr__(self, "detail", clamp_text(self.detail, 1_000))
        object.__setattr__(
            self, "expected_result", clamp_text(self.expected_result, 400)
        )
        object.__setattr__(self, "tools", tuple(dict.fromkeys(self.tools))[:20])
        object.__setattr__(
            self, "permissions", tuple(dict.fromkeys(self.permissions))[:20]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "detail": self.detail,
            "expected_result": self.expected_result,
            "tools": list(self.tools),
            "permissions": list(self.permissions),
        }


@dataclass(frozen=True)
class TaskPlan:
    """Version immuable d'un plan. Modifier un plan crée une nouvelle version.

    Deux listes de permissions cohabitent, et elles ne disent pas la même
    chose. ``permissions_expected`` est **annoncé par le planificateur** : il
    prévient qu'un effet externe (`mail:send`, `git:push`…) demandera plus tard
    son autorisation propre. ``execution_permissions`` est la **liste canonique
    exacte remise au runtime** au démarrage : c'est elle que l'utilisateur
    approuve, elle entre dans le digest, et le lancement est refusé si le
    routage en réclame une autre.
    """

    plan_id: str
    task_id: str
    version: int
    objective: str
    summary: str
    context_understood: str = ""
    steps: tuple[PlanStep, ...] = ()
    expected_deliverables: tuple[str, ...] = ()
    tools_expected: tuple[str, ...] = ()
    permissions_expected: tuple[str, ...] = ()
    execution_permissions: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    known_limits: tuple[str, ...] = ()
    estimated_duration_s: int | None = None
    estimated_cost: float | None = None
    created_by: str = "jarvis.planner"
    created_at: datetime = field(default_factory=utc_now)
    decision: PlanDecision = PlanDecision.PENDING
    decision_at: datetime | None = None
    decision_by: str | None = None
    decision_comment: str = ""
    digest: str = ""

    def __post_init__(self) -> None:
        validate_identifier(self.plan_id, label="plan_id")
        validate_identifier(self.task_id, label="task_id")
        if self.version < 1:
            raise ValueError("version de plan >= 1")
        object.__setattr__(self, "objective", clamp_text(self.objective, 1_000))
        object.__setattr__(self, "summary", clamp_text(self.summary, 2_000))
        object.__setattr__(
            self, "context_understood", clamp_text(self.context_understood, 2_000)
        )
        if not self.objective:
            raise ValueError("objectif de plan requis")
        object.__setattr__(self, "steps", tuple(self.steps)[:MAX_STEPS])
        if not self.steps:
            raise ValueError("un plan comporte au moins une étape")
        for name in (
            "expected_deliverables",
            "tools_expected",
            "permissions_expected",
            "execution_permissions",
            "risks",
            "assumptions",
            "success_criteria",
            "known_limits",
        ):
            values = tuple(
                clamp_text(item, 300) for item in getattr(self, name) if str(item).strip()
            )
            object.__setattr__(self, name, tuple(dict.fromkeys(values))[:20])
        object.__setattr__(
            self, "decision_comment", clamp_text(self.decision_comment, 1_000)
        )
        if self.estimated_duration_s is not None and self.estimated_duration_s < 0:
            raise ValueError("durée estimée négative")
        if self.estimated_cost is not None and self.estimated_cost < 0:
            raise ValueError("coût estimé négatif")
        object.__setattr__(self, "digest", self.digest or compute_plan_digest(self))

    @property
    def approved(self) -> bool:
        return self.decision is PlanDecision.APPROVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task_id": self.task_id,
            "version": self.version,
            "objective": self.objective,
            "summary": self.summary,
            "context_understood": self.context_understood,
            "steps": [step.to_dict() for step in self.steps],
            "expected_deliverables": list(self.expected_deliverables),
            "tools_expected": list(self.tools_expected),
            "permissions_expected": list(self.permissions_expected),
            "execution_permissions": list(self.execution_permissions),
            "risks": list(self.risks),
            "assumptions": list(self.assumptions),
            "success_criteria": list(self.success_criteria),
            "known_limits": list(self.known_limits),
            "estimated_duration_s": self.estimated_duration_s,
            "estimated_cost": self.estimated_cost,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "decision": self.decision.value,
            "decision_at": self.decision_at.isoformat() if self.decision_at else None,
            "decision_by": self.decision_by,
            "decision_comment": self.decision_comment,
            "digest": self.digest,
        }


def compute_plan_digest(plan: TaskPlan) -> str:
    """Empreinte du *contenu exécutable* du plan, hors décision et horodatage.

    Le digest ne couvre volontairement ni la décision ni ses métadonnées :
    il doit rester stable entre le moment où l'utilisateur lit le plan et
    celui où l'exécution démarre, tout en changeant dès qu'une étape, un outil
    ou une permission bouge.
    """

    payload = {
        "task_id": plan.task_id,
        "version": plan.version,
        "objective": plan.objective,
        "steps": [step.to_dict() for step in plan.steps],
        "expected_deliverables": list(plan.expected_deliverables),
        "tools_expected": list(plan.tools_expected),
        "permissions_expected": list(plan.permissions_expected),
        "execution_permissions": list(plan.execution_permissions),
        "success_criteria": list(plan.success_criteria),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TaskSource:
    """Provenance d'une tâche — traçable, redactée, jamais recopiée en entier."""

    source_type: TaskSourceType = TaskSourceType.MANUAL
    channel: TaskSourceChannel = TaskSourceChannel.API
    reference: str = ""
    excerpt: str = ""
    confidence: float | None = None
    detection_reason: str = ""
    sender: str = ""
    subject: str = ""
    occurred_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference", clamp_text(self.reference, 256))
        object.__setattr__(self, "excerpt", clamp_text(self.excerpt, MAX_EXCERPT_CHARS))
        object.__setattr__(
            self, "detection_reason", clamp_text(self.detection_reason, 300)
        )
        object.__setattr__(self, "sender", clamp_text(self.sender, 200))
        object.__setattr__(self, "subject", clamp_text(self.subject, 300))
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence hors [0,1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type.value,
            "channel": self.channel.value,
            "reference": self.reference,
            "excerpt": self.excerpt,
            "confidence": self.confidence,
            "detection_reason": self.detection_reason,
            "sender": self.sender,
            "subject": self.subject,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
        }


@dataclass(frozen=True)
class ControlTask:
    """Tâche pilotée. La vérité métier vit ici, jamais dans le client."""

    task_id: str
    profile_id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.CREATED
    priority: TaskPriority = TaskPriority.MEDIUM
    source: TaskSource = field(default_factory=TaskSource)
    project_id: str | None = None
    conversation_id: str | None = None
    due_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    plan_id: str | None = None
    plan_version: int | None = None
    approved_plan_version: int | None = None
    approved_plan_digest: str | None = None
    agentic_run_id: str | None = None
    current_phase: str = ""
    progress: float = 0.0
    attention_required: bool = False
    result_status: str | None = None
    final_report_id: str | None = None
    legacy_task_id: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.task_id, label="task_id")
        object.__setattr__(self, "title", clamp_text(self.title, MAX_TITLE_CHARS))
        if not self.title:
            raise ValueError("titre de tâche requis")
        object.__setattr__(
            self, "description", clamp_text(self.description, MAX_DESCRIPTION_CHARS)
        )
        object.__setattr__(self, "current_phase", clamp_text(self.current_phase, 120))
        object.__setattr__(self, "progress", max(0.0, min(1.0, float(self.progress))))
        object.__setattr__(self, "metadata", _frozen(self.metadata))

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_TASK_STATUSES

    @property
    def needs_attention(self) -> bool:
        return self.attention_required or self.status in ATTENTION_TASK_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "profile_id": self.profile_id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "source": self.source.to_dict(),
            "source_type": self.source.source_type.value,
            "source_channel": self.source.channel.value,
            "project_id": self.project_id,
            "conversation_id": self.conversation_id,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "approved_plan_version": self.approved_plan_version,
            "approved_plan_digest": self.approved_plan_digest,
            "agentic_run_id": self.agentic_run_id,
            "current_phase": self.current_phase,
            "progress": self.progress,
            "attention_required": self.needs_attention,
            "result_status": self.result_status,
            "final_report_id": self.final_report_id,
            "legacy_task_id": self.legacy_task_id,
        }


def ensure_executable(task: ControlTask, plan: TaskPlan | None) -> TaskPlan:
    """Seule porte vers l'exécution. Échoue plutôt que de démarrer dans le doute.

    Quatre conditions doivent tenir *ensemble*. Les vérifier séparément ailleurs
    laisserait la place à un chemin qui en oublie une : c'est précisément ce
    qu'une revue ne rattrape pas.
    """

    if plan is None:
        raise TaskExecutionRefused("aucun plan n'est attaché à cette tâche")
    if plan.task_id != task.task_id:
        raise TaskExecutionRefused("le plan n'appartient pas à cette tâche")
    if not plan.approved:
        raise TaskExecutionRefused("le plan n'a pas été approuvé")
    if task.approved_plan_version != plan.version:
        raise TaskExecutionRefused(
            "la version approuvée ne correspond pas au plan à exécuter"
        )
    if not task.approved_plan_digest or task.approved_plan_digest != plan.digest:
        raise TaskExecutionRefused(
            "le plan a changé depuis son approbation ; une nouvelle validation est requise"
        )
    if task.status not in {TaskStatus.APPROVED, TaskStatus.QUEUED}:
        raise TaskExecutionRefused(
            f"l'état {task.status.value} n'autorise pas le démarrage"
        )
    return plan


def ensure_permission_fidelity(
    plan: TaskPlan, resolved: Sequence[str]
) -> tuple[str, ...]:
    """Le run reçoit la liste approuvée, ou rien ne démarre.

    Approuver un plan, c'est approuver des capacités d'exécution nommées. Si le
    routage recalcule une autre liste au moment du départ — plus large comme
    plus étroite — l'utilisateur n'a pas consenti à *cette* exécution : le
    démarrage est refusé avant toute création de runtime, et une élévation
    légitime passe par une nouvelle version de plan donc une nouvelle décision.

    Un plan approuvé sans liste est un plan écrit avant ce contrat. Il est
    refusé plutôt que rattrapé : lui prêter la liste recalculée reviendrait à
    accorder des droits que personne n'a lus.
    """

    approved = tuple(plan.execution_permissions)
    if not approved:
        raise TaskExecutionRefused(
            "ce plan a été approuvé sans liste d'autorisations d'exécution ; "
            "demandez une nouvelle version du plan"
        )
    if approved != tuple(dict.fromkeys(str(item) for item in resolved)):
        raise TaskExecutionRefused(
            "les autorisations d'exécution ont changé depuis l'approbation ; "
            "une nouvelle version du plan doit être validée"
        )
    return approved


@dataclass(frozen=True)
class TaskActivity:
    """Entrée du journal d'activité — résumé sûr, jamais une chaîne de pensée."""

    activity_id: str
    task_id: str
    sequence: int
    event_type: TaskActivityType
    summary: str
    agent_id: str = ""
    agent_role: str = ""
    phase: str = ""
    tool_name: str = ""
    artifact_reference: str = ""
    status: str = ""
    level: TaskActivityLevel = TaskActivityLevel.DETAIL
    run_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_identifier(self.activity_id, label="activity_id")
        validate_identifier(self.task_id, label="task_id")
        object.__setattr__(self, "summary", clamp_text(self.summary, MAX_SUMMARY_CHARS))
        for name, limit in (
            ("agent_id", 120),
            ("agent_role", 80),
            ("phase", 120),
            ("tool_name", 120),
            ("artifact_reference", 512),
            ("status", 60),
        ):
            object.__setattr__(self, name, clamp_text(getattr(self, name), limit))

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity_id": self.activity_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "event_type": self.event_type.value,
            "summary": self.summary,
            "agent_id": self.agent_id,
            "agent_role": self.agent_role,
            "phase": self.phase,
            "tool_name": self.tool_name,
            "artifact_reference": self.artifact_reference,
            "status": self.status,
            "level": self.level.value,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class TaskCandidate:
    """Détection non confirmée. Ne peut rien exécuter, ni répondre à personne."""

    candidate_id: str
    profile_id: str
    suggested_title: str
    suggested_description: str = ""
    source: TaskSource = field(default_factory=TaskSource)
    confidence: float = 0.0
    reason: str = ""
    suggested_due_at: datetime | None = None
    decision: CandidateDecision = CandidateDecision.PENDING
    decision_at: datetime | None = None
    created_task_id: str | None = None
    duplicate_of: str | None = None
    dedupe_key: str = ""
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_identifier(self.candidate_id, label="candidate_id")
        object.__setattr__(
            self, "suggested_title", clamp_text(self.suggested_title, MAX_TITLE_CHARS)
        )
        if not self.suggested_title:
            raise ValueError("titre suggéré requis")
        object.__setattr__(
            self, "suggested_description", clamp_text(self.suggested_description, 2_000)
        )
        object.__setattr__(self, "reason", clamp_text(self.reason, 300))
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence hors [0,1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "profile_id": self.profile_id,
            "suggested_title": self.suggested_title,
            "suggested_description": self.suggested_description,
            "source": self.source.to_dict(),
            "confidence": self.confidence,
            "reason": self.reason,
            "suggested_due_at": (
                self.suggested_due_at.isoformat() if self.suggested_due_at else None
            ),
            "decision": self.decision.value,
            "decision_at": self.decision_at.isoformat() if self.decision_at else None,
            "created_task_id": self.created_task_id,
            "duplicate_of": self.duplicate_of,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class TaskReport:
    """Rapport final, immuable par version. JARVIS l'écrit, pas le runtime."""

    report_id: str
    task_id: str
    version: int
    result_status: str
    markdown: str
    summary: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_identifier(self.report_id, label="report_id")
        validate_identifier(self.task_id, label="task_id")
        if self.version < 1:
            raise ValueError("version de rapport >= 1")
        object.__setattr__(self, "summary", clamp_text(self.summary, MAX_SUMMARY_CHARS))
        object.__setattr__(self, "data", _frozen(self.data))

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "task_id": self.task_id,
            "version": self.version,
            "result_status": self.result_status,
            "summary": self.summary,
            "markdown": self.markdown,
            "data": dict(self.data),
            "created_at": self.created_at.isoformat(),
        }


__all__ = [
    "ALLOWED_TASK_TRANSITIONS",
    "ATTENTION_TASK_STATUSES",
    "EXECUTING_TASK_STATUSES",
    "TERMINAL_TASK_STATUSES",
    "ApprovalKind",
    "CandidateDecision",
    "ControlTask",
    "InvalidTaskTransition",
    "PlanDecision",
    "PlanStep",
    "TaskActivity",
    "TaskActivityLevel",
    "TaskActivityType",
    "TaskCandidate",
    "TaskExecutionRefused",
    "TaskPlan",
    "TaskPriority",
    "TaskReport",
    "TaskSource",
    "TaskSourceChannel",
    "TaskSourceType",
    "TaskStatus",
    "clamp_text",
    "compute_plan_digest",
    "ensure_executable",
    "ensure_permission_fidelity",
    "new_id",
    "utc_now",
    "validate_identifier",
    "validate_task_transition",
]

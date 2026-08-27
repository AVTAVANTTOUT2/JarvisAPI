"""Service de pilotage des tâches — propriétaire de la vérité métier.

Ce service est le seul chemin entre une demande et une exécution. Il possède
les tâches, les plans, les décisions, l'activité et les rapports ; il délègue
l'exécution à ``jarvis.agentic`` sans jamais nommer un fournisseur.

L'invariant du produit est appliqué à un seul endroit — ``_launch_run`` —
et il y est appliqué par ``ensure_executable``, qui exige simultanément un
plan approuvé, la version approuvée, le digest exact et un état compatible.
Aucun autre chemin de ce module n'appelle le runtime.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
import logging
from pathlib import Path
from typing import Any, Iterable, Mapping, NamedTuple, Sequence
import uuid

from database.core import current_profile_id
from database.task_control import (
    TaskControlRepository,
    TaskNotFound,
    TaskPersistenceConflict,
)
from jarvis.agentic.redaction import redact_text
from jarvis.event_bus import AGENTIC_EVENT_TYPES, EventBus, JarvisEvent, event_bus
from jarvis.notification_service import NotificationService, notification_service

from .activity import build_activity, build_user_activity
from .detection import DetectedTask, TaskCandidateDetector, detector_from_config
from .events import emit_task_control_event
from .models import (
    ApprovalKind,
    CandidateDecision,
    ControlTask,
    InvalidTaskTransition,
    PlanDecision,
    TaskActivity,
    TaskActivityLevel,
    TaskActivityType,
    TaskCandidate,
    TaskPlan,
    TaskPriority,
    TaskSource,
    TaskSourceChannel,
    TaskSourceType,
    TaskStatus,
    clamp_text,
    new_id,
)
from .planner import generate_plan
from .reports import build_report, result_status_for

logger = logging.getLogger("jarvis")

#: État de run agentique → état de tâche. Le run reste la source de vérité de
#: son propre avancement ; la tâche en est la lecture métier.
_RUN_STATUS_TO_TASK: dict[str, TaskStatus] = {
    "created": TaskStatus.QUEUED,
    "classified": TaskStatus.QUEUED,
    "queued": TaskStatus.QUEUED,
    "resource_wait": TaskStatus.RESOURCE_WAIT,
    # `provisioning` prépare l'espace de travail : le runtime n'a pas encore
    # démarré. L'afficher « en cours » annonçait un travail qui n'avait pas
    # commencé — `running` n'arrive qu'avec `agent.run.started`.
    "provisioning": TaskStatus.QUEUED,
    "planning": TaskStatus.RUNNING,
    "running": TaskStatus.RUNNING,
    "awaiting_approval": TaskStatus.AWAITING_PERMISSION,
    # Une pause attend un geste humain pour reprendre : c'est de l'attention
    # requise, pas un simple ralentissement, et l'interface doit le dire.
    "paused": TaskStatus.BLOCKED,
    "blocked": TaskStatus.BLOCKED,
    "verifying": TaskStatus.VERIFYING,
    "reviewing": TaskStatus.VERIFYING,
    "cancelling": TaskStatus.CANCELLING,
    "cancelled": TaskStatus.CANCELLED,
    "completed": TaskStatus.COMPLETED,
    "failed": TaskStatus.FAILED,
    "expired": TaskStatus.FAILED,
    "provider_unavailable": TaskStatus.FAILED,
}

#: L'attente d'admission n'est pas un état de run : le run reste `queued` et
#: c'est le **type** d'événement qui porte l'information. Sans cette table, une
#: tâche retenue par la mémoire s'affichait simplement « en file ».
_RUN_EVENT_TO_TASK: dict[str, TaskStatus] = {
    "agent.run.resource_wait": TaskStatus.RESOURCE_WAIT,
}


def task_status_for_run(status_value: str, *, event_type: str = "") -> TaskStatus | None:
    """Seule conversion état de run → état de tâche, pour tous les appelants."""

    return _RUN_EVENT_TO_TASK.get(event_type) or _RUN_STATUS_TO_TASK.get(status_value)


_TERMINAL_RESULTS = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.BLOCKED,
}

_NOTIFICATION_BY_STATUS: dict[TaskStatus, tuple[str, str, str]] = {
    TaskStatus.AWAITING_PLAN_APPROVAL: (
        "Plan prêt à être vérifié",
        "Un plan attend votre validation avant tout démarrage.",
        "high",
    ),
    TaskStatus.AWAITING_PERMISSION: (
        "Autorisation requise",
        "Une autorisation est nécessaire pour continuer la tâche.",
        "urgent",
    ),
    TaskStatus.BLOCKED: (
        "Tâche bloquée",
        "La tâche attend une intervention pour continuer.",
        "high",
    ),
    TaskStatus.FAILED: (
        "Tâche en échec",
        "La tâche s'est arrêtée sans aboutir.",
        "high",
    ),
    TaskStatus.COMPLETED: (
        "Tâche terminée",
        "Le résultat est disponible dans la tâche.",
        "medium",
    ),
}


class ExecutionGrant(NamedTuple):
    """Ce que le runtime recevra : catégorie, profil, permissions exactes."""

    category: Any
    capability_profile_id: str
    permissions: tuple[str, ...]


def resolve_execution_grant(task: ControlTask, objective: str) -> ExecutionGrant:
    """Calcule la borne de capacités d'une tâche, sans rien démarrer.

    Une seule fonction, appelée deux fois : à la planification pour écrire dans
    le plan ce que l'utilisateur va approuver, puis au démarrage pour vérifier
    que rien n'a bougé. C'est la racine du défaut corrigé ici — le plan et le
    run lisaient chacun leur propre source de permissions.

    La résolution est déterministe (classification par mots-clés, table de
    profils figée, surcharges de configuration), donc deux appels sur le même
    objectif et les mêmes métadonnées donnent la même liste.
    """

    import config
    from jarvis.agentic import (
        classify_agentic_request,
        get_capability_profile,
        select_capability_profile,
    )
    from jarvis.agentic.profiles import constrain_capability_profile_for_request
    from jarvis.agentic.models import AgenticRequestCategory
    from jarvis.agentic.turn_context import AGENTIC_ROUTING_METADATA_KEY

    routing_raw = task.metadata.get(AGENTIC_ROUTING_METADATA_KEY)
    routing = dict(routing_raw) if isinstance(routing_raw, Mapping) else {}
    try:
        category = AgenticRequestCategory(str(routing.get("category") or ""))
    except ValueError:
        category = classify_agentic_request(
            objective, origin="user", adaptive=True
        ).category
    capability_profile_id = str(routing.get("capability_profile_id") or "").strip()
    if capability_profile_id:
        # Le profil persisté est ré-borné par les interdictions de l'objectif.
        # Sans cela, un identifiant de profil enregistré rendrait au run une
        # capacité que la demande interdisait explicitement.
        capability_profile = constrain_capability_profile_for_request(
            get_capability_profile(capability_profile_id), objective
        )
    else:
        capability_profile = select_capability_profile(
            objective,
            category,
            default_profile_id=str(
                getattr(config, "AGENTIC_DEFAULT_PROFILE", "readonly-research")
            ),
            route_overrides=getattr(config, "AGENTIC_PROFILE_ROUTE_OVERRIDES", {}),
        )
        capability_profile_id = capability_profile.profile_id
    raw_permissions = routing.get("permissions")
    permissions = (
        tuple(
            dict.fromkeys(
                str(item) for item in raw_permissions if isinstance(item, str) and item
            )
        )
        if isinstance(raw_permissions, (list, tuple))
        else capability_profile.default_permissions
    )
    if not permissions:
        permissions = capability_profile.default_permissions
    if capability_profile.refused_permissions(permissions):
        raise PermissionError("permission hors du profil de capacités JARVIS")
    return ExecutionGrant(category, capability_profile_id, permissions)


class TaskControlService:
    """Orchestrateur. Ne connaît aucun fournisseur d'exécution."""

    def __init__(
        self,
        *,
        repository: TaskControlRepository | None = None,
        agentic_service: Any | None = None,
        detector: TaskCandidateDetector | None = None,
        bus: EventBus = event_bus,
        notifications: NotificationService = notification_service,
        planner: Any = generate_plan,
    ) -> None:
        self.repository = repository or TaskControlRepository()
        self._agentic_service = agentic_service
        self.detector = detector or detector_from_config()
        self.bus = bus
        self.notifications = notifications
        self._planner = planner
        self._locks: dict[str, asyncio.Lock] = {}
        self._subscribed = False

    # ── Infrastructure ────────────────────────────────────────────────────

    def _lock(self, task_id: str) -> asyncio.Lock:
        return self._locks.setdefault(task_id, asyncio.Lock())

    @property
    def agentic(self) -> Any:
        if self._agentic_service is None:
            from jarvis.agentic import get_agentic_service

            self._agentic_service = get_agentic_service()
        return self._agentic_service

    def bind_runtime_events(self) -> None:
        """Branche la traduction des événements runtime en activité de tâche."""

        if self._subscribed:
            return
        self._subscribed = True

        @self.bus.on(list(AGENTIC_EVENT_TYPES))
        async def _on_agentic_event(event: JarvisEvent) -> None:  # pragma: no cover
            try:
                await self.on_runtime_event(event.type, dict(event.data or {}))
            except Exception:
                logger.exception("traduction d'événement agentique impossible")

    # ── Création ──────────────────────────────────────────────────────────

    async def create_task(
        self,
        *,
        title: str,
        description: str = "",
        priority: TaskPriority | str = TaskPriority.MEDIUM,
        source: TaskSource | None = None,
        project_id: str | None = None,
        conversation_id: str | None = None,
        due_at: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
        planning_context: Mapping[str, Any] | None = None,
        autoplan: bool = True,
    ) -> ControlTask:
        """Crée une tâche **en attente de plan**. Ne démarre jamais rien."""

        task = ControlTask(
            task_id=new_id("task"),
            profile_id=current_profile_id(),
            title=title,
            description=description,
            status=TaskStatus.CREATED,
            priority=TaskPriority(priority),
            source=source or TaskSource(),
            project_id=project_id,
            conversation_id=conversation_id,
            due_at=due_at,
            metadata=metadata or {},
        )
        task = self.repository.create_task(task)
        self.repository.append_activity(
            build_user_activity(
                task_id=task.task_id,
                summary=f"Tâche créée depuis {task.source.source_type.value}.",
                event_type=TaskActivityType.AGENT_SUMMARY,
            )
        )
        await self._emit(task, "task.control.created")
        if autoplan:
            task = await self.plan_task(task.task_id, context=planning_context)
        return task

    async def create_engineering_task(
        self,
        *,
        title: str,
        user_request: str,
        repo_root: Path,
        required_tests: Sequence[str | Sequence[str]],
        acceptance_criteria: Sequence[str] = (),
        commit_message: str | None = None,
        idempotency_key: str | None = None,
        runtime_id: str,
        runtime_version: str,
        source: TaskSource | None = None,
        conversation_id: str | None = None,
        autoplan: bool = True,
    ) -> ControlTask:
        """Crée une livraison de code interne, sans préparer ni lancer quoi que ce soit.

        La route générique ``POST /api/tasks`` n'expose pas ce contrat. Les
        validations, le dépôt et l'identité du runtime sont normalisés ici puis
        liés au futur plan par une empreinte déterministe.
        """

        from jarvis.agentic.turn_context import AGENTIC_ROUTING_METADATA_KEY

        from .engineering import (
            ENGINEERING_DELIVERY_METADATA_KEY,
            build_engineering_delivery_contract,
        )

        request = str(user_request or "").strip()
        if not request:
            raise ValueError("demande de développement requise")
        stable_key = idempotency_key or f"task-control-engineering:{uuid.uuid4().hex}"
        contract = build_engineering_delivery_contract(
            repo_root=repo_root,
            required_tests=required_tests,
            acceptance_criteria=acceptance_criteria,
            commit_message=commit_message or title,
            idempotency_key=stable_key,
            runtime_id=runtime_id,
            runtime_version=runtime_version,
        )
        metadata = {
            ENGINEERING_DELIVERY_METADATA_KEY: contract.to_metadata(),
            AGENTIC_ROUTING_METADATA_KEY: {
                "category": "agentic_reversible",
                "capability_profile_id": "coding",
                "permissions": [
                    "workspace:read",
                    "workspace:write",
                    "tests:run",
                ],
                "reason": "livraison de code confinée pilotée par Task Control",
            },
        }
        return await self.create_task(
            title=title,
            description=request,
            source=source
            or TaskSource(
                source_type=TaskSourceType.USER_REQUEST,
                channel=TaskSourceChannel.API,
                excerpt=clamp_text(request, 400),
            ),
            conversation_id=conversation_id,
            metadata=metadata,
            planning_context={
                "engineering_runtime": contract.runtime_label,
                "engineering_validations": [
                    list(command) for command in contract.required_tests
                ],
                "engineering_acceptance_criteria": list(
                    contract.acceptance_criteria
                ),
            },
            autoplan=autoplan,
        )

    # ── Planification (lecture seule) ─────────────────────────────────────

    async def plan_task(
        self,
        task_id: str,
        *,
        revision_comment: str = "",
        context: Mapping[str, Any] | None = None,
    ) -> ControlTask:
        """Produit une version de plan et attend la décision humaine."""

        async with self._lock(task_id):
            task = self.repository.require_task(task_id)
            if task.status in {
                TaskStatus.CREATED,
                TaskStatus.PLAN_REJECTED,
                TaskStatus.PLAN_REVISION_REQUESTED,
                TaskStatus.FAILED,
            }:
                task = self.repository.update_task(
                    task_id, status=TaskStatus.PLANNING, current_phase="planning"
                )
            elif task.status is not TaskStatus.PLANNING:
                raise InvalidTaskTransition(
                    f"planification impossible depuis {task.status.value}"
                )

            version = self.repository.next_plan_version(task_id)
            planning_context: dict[str, Any] = {}
            try:
                from jarvis.agentic.turn_context import (
                    SNAPSHOT_METADATA_KEY,
                    TurnKnowledgeSnapshot,
                )

                snapshot = TurnKnowledgeSnapshot.from_metadata(
                    task.metadata.get(SNAPSHOT_METADATA_KEY),
                    expected_profile_id=task.profile_id,
                )
                if snapshot is not None:
                    planning_context.update(snapshot.planning_context())
            except PermissionError:
                raise
            except Exception as exc:
                logger.warning(
                    "snapshot de planification invalide pour %s: %s",
                    task_id,
                    type(exc).__name__,
                )
            planning_context.update(dict(context or {}))
            if revision_comment:
                planning_context["user_comments"] = revision_comment
            comments = self.repository.list_comments(task_id, limit=10)
            if comments:
                planning_context.setdefault(
                    "user_comments",
                    " | ".join(item["body"] for item in comments[-5:]),
                )

            try:
                plan: TaskPlan = await self._planner(
                    task, version=version, context=planning_context
                )
                from .engineering import (
                    bind_engineering_contract_to_plan,
                    engineering_delivery_contract_from_metadata,
                )

                engineering = engineering_delivery_contract_from_metadata(
                    task.metadata
                )
                if engineering is not None:
                    plan = bind_engineering_contract_to_plan(plan, engineering)
                # Le plan que l'utilisateur va lire annonce les capacités
                # exactes du futur run. Les recalculer au démarrage sans les
                # avoir écrites ici, c'était faire approuver autre chose que
                # ce qui s'exécute.
                plan = replace(
                    plan,
                    execution_permissions=resolve_execution_grant(
                        task, plan.objective
                    ).permissions,
                    digest="",
                )
            except Exception:
                logger.exception("planification impossible pour %s", task_id)
                task = self.repository.update_task(
                    task_id,
                    status=TaskStatus.BLOCKED,
                    current_phase="planning_failed",
                    attention_required=True,
                )
                await self._emit(task, "task.control.blocked")
                self._notify(task)
                return task

            plan = self.repository.save_plan(plan)
            task = self.repository.update_task(
                task_id,
                status=TaskStatus.AWAITING_PLAN_APPROVAL,
                plan_id=plan.plan_id,
                plan_version=plan.version,
                current_phase="awaiting_plan_approval",
                attention_required=True,
            )
            self.repository.append_activity(
                build_user_activity(
                    task_id=task_id,
                    summary=f"Plan v{plan.version} proposé — {len(plan.steps)} étape(s).",
                    event_type=TaskActivityType.DECISION_SUMMARY,
                )
            )
            await self._emit(
                task,
                "task.control.plan_ready",
                plan_version=plan.version,
                spoken_summary="Un plan attend votre validation.",
            )
            self._notify(task)
            return task

    async def decide_plan(
        self,
        task_id: str,
        version: int,
        *,
        decision: PlanDecision | str,
        actor: str,
        comment: str = "",
        autostart: bool = True,
    ) -> ControlTask:
        """Tranche une version de plan. L'approbation est la seule porte d'entrée."""

        wanted = PlanDecision(decision)
        if wanted not in {
            PlanDecision.APPROVED,
            PlanDecision.REJECTED,
            PlanDecision.REVISION_REQUESTED,
        }:
            raise ValueError("décision de plan non recevable")

        async with self._lock(task_id):
            task = self.repository.require_task(task_id)
            if task.status is not TaskStatus.AWAITING_PLAN_APPROVAL:
                raise InvalidTaskTransition(
                    f"aucune décision attendue dans l'état {task.status.value}"
                )
            plan = self.repository.get_plan(task_id, version)
            if plan is None:
                raise TaskNotFound(f"{task_id}#{version}")
            if task.plan_version != plan.version:
                # L'écran affichait une autre version : refuser plutôt que
                # d'approuver un plan que l'utilisateur n'a pas sous les yeux.
                raise TaskPersistenceConflict(
                    "la version décidée n'est pas la version courante du plan"
                )

            plan = self.repository.decide_plan(
                task_id, version, decision=wanted, actor=actor, comment=comment
            )
            if comment:
                self.repository.add_comment(
                    comment_id=new_id("cmt"),
                    task_id=task_id,
                    body=comment,
                    author=actor,
                    plan_version=version,
                )

            if wanted is PlanDecision.APPROVED:
                task = self.repository.update_task(
                    task_id,
                    status=TaskStatus.APPROVED,
                    approved_plan_version=plan.version,
                    approved_plan_digest=plan.digest,
                    current_phase="approved",
                    attention_required=False,
                )
                summary = f"Plan v{plan.version} approuvé."
            elif wanted is PlanDecision.REJECTED:
                task = self.repository.update_task(
                    task_id,
                    status=TaskStatus.PLAN_REJECTED,
                    current_phase="plan_rejected",
                    attention_required=False,
                    result_status="plan_rejected",
                )
                summary = f"Plan v{plan.version} refusé."
            else:
                task = self.repository.update_task(
                    task_id,
                    status=TaskStatus.PLAN_REVISION_REQUESTED,
                    current_phase="plan_revision_requested",
                    attention_required=False,
                )
                summary = f"Révision demandée sur le plan v{plan.version}."

            self.repository.append_activity(
                build_user_activity(
                    task_id=task_id,
                    summary=summary,
                    event_type=TaskActivityType.DECISION_SUMMARY,
                )
            )
            await self._emit(
                task,
                "task.control.plan_decided",
                plan_version=plan.version,
                decision=wanted.value,
                spoken_summary=summary,
            )

        if wanted is PlanDecision.REVISION_REQUESTED:
            return await self.plan_task(task_id, revision_comment=comment)
        if wanted is PlanDecision.APPROVED and autostart:
            return await self.start_execution(task_id)
        return self.repository.require_task(task_id)

    # ── Exécution ─────────────────────────────────────────────────────────

    async def _record_launch_failure(
        self,
        task_id: str,
        exc: Exception,
        *,
        phase: str,
        run_id: str | None = None,
    ) -> ControlTask:
        task = self.repository.update_task(
            task_id,
            agentic_run_id=run_id,
            status=TaskStatus.FAILED,
            current_phase=phase,
            attention_required=True,
            result_status="failed",
        )
        self.repository.append_activity(
            build_user_activity(
                task_id=task_id,
                summary=f"Démarrage impossible ({type(exc).__name__}).",
                event_type=TaskActivityType.ERROR,
                run_id=run_id,
            )
        )
        await self._emit(task, "task.control.failed")
        self._notify(task)
        await self.finalize(task_id, error="Le runtime n'a pas pu démarrer.")
        return self.repository.require_task(task_id)

    async def start_execution(self, task_id: str) -> ControlTask:
        """Démarre l'exécution. Refuse tout ce qui n'a pas été approuvé."""

        async with self._lock(task_id):
            return await self._launch_run(task_id)

    async def _launch_run(self, task_id: str) -> ControlTask:
        from jarvis.agentic.turn_context import (
            AGENTIC_ROUTING_METADATA_KEY,
            SNAPSHOT_METADATA_KEY,
            TurnKnowledgeSnapshot,
        )

        from .models import ensure_executable, ensure_permission_fidelity
        from .engineering import (
            engineering_delivery_contract_from_metadata,
            ensure_engineering_contract_approved,
        )

        task = self.repository.require_task(task_id)
        resume_run_id: str | None = None
        if task.agentic_run_id:
            try:
                existing_run = self.agentic.get(task.agentic_run_id)
            except Exception:
                existing_run = None
            # Un run réellement persisté gagne toujours. En revanche, une
            # association QUEUED sans run est une intention de lancement
            # interrompue : elle doit reprendre avec le même identifiant.
            if existing_run is not None or task.status is not TaskStatus.QUEUED:
                return task
            resume_run_id = task.agentic_run_id
        plan = (
            self.repository.get_plan(task_id, task.approved_plan_version)
            if task.approved_plan_version
            else None
        )
        approved = ensure_executable(task, plan)
        engineering = engineering_delivery_contract_from_metadata(task.metadata)
        if engineering is not None:
            ensure_engineering_contract_approved(approved, engineering)
            registry = getattr(self.agentic, "registry", None)
            manifest_getter = getattr(registry, "manifest", None)
            if callable(manifest_getter):
                active_manifest = manifest_getter(engineering.runtime_id)
                if (
                    active_manifest is None
                    or str(getattr(active_manifest, "version", ""))
                    != engineering.runtime_version
                ):
                    reason = (
                        "Runtime approuvé indisponible ou version modifiée ; "
                        "une nouvelle version du plan doit être approuvée."
                    )
                    task = self.repository.update_task(
                        task_id,
                        status=TaskStatus.PLAN_REVISION_REQUESTED,
                        approved_plan_version=None,
                        approved_plan_digest=None,
                        agentic_run_id=None,
                        current_phase="runtime_contract_changed",
                        attention_required=True,
                    )
                    self.repository.append_activity(
                        build_user_activity(
                            task_id=task_id,
                            summary=reason,
                            event_type=TaskActivityType.WARNING,
                        )
                    )
                    await self._emit(
                        task,
                        "task.control.plan_revision_requested",
                        spoken_summary=reason,
                    )
                    return task

        routing_raw = task.metadata.get(AGENTIC_ROUTING_METADATA_KEY)
        routing = dict(routing_raw) if isinstance(routing_raw, Mapping) else {}
        grant = resolve_execution_grant(task, approved.objective)
        # Le run reçoit littéralement la liste approuvée. Aucune permission
        # n'est ajoutée ici : toute divergence avec le recalcul est refusée
        # avant qu'un runtime existe.
        permissions = ensure_permission_fidelity(approved, grant.permissions)
        category = grant.category
        capability_profile_id = grant.capability_profile_id

        selected_context: dict[str, Any] = {
            "request": approved.objective,
            "plan_version": approved.version,
            "plan_digest": approved.digest,
            "plan_steps": [step.title for step in approved.steps],
        }
        snapshot = TurnKnowledgeSnapshot.from_metadata(
            task.metadata.get(SNAPSHOT_METADATA_KEY),
            expected_profile_id=task.profile_id,
        )
        if snapshot is not None:
            selected_context.update(snapshot.agentic_context())
        classification_reason = str(routing.get("reason") or "").strip()
        if classification_reason:
            selected_context["classification"] = classification_reason

        worktree = None
        if engineering is not None:
            from agents.devagent.agentic_runtime import (
                build_engineering_instruction,
                prepare_engineering_worktree,
            )

            # Cette première mutation (branche + worktree) se trouve après
            # ``ensure_executable`` et l'empreinte approuvée, jamais au plan.
            try:
                worktree = prepare_engineering_worktree(
                    repo_root=engineering.repo_root,
                    job_id=engineering.job_id,
                    reuse_existing=True,
                )
            except Exception as exc:
                logger.exception("préparation du worktree impossible pour %s", task_id)
                return await self._record_launch_failure(
                    task_id, exc, phase="worktree_failed"
                )
            selected_context.update(
                {
                    "request": build_engineering_instruction(
                        user_request=task.description,
                        acceptance_criteria=engineering.acceptance_criteria,
                        evidence={
                            "task_id": task.task_id,
                            "plan_version": approved.version,
                            "plan_digest": approved.digest,
                        },
                    ),
                    "jarvis_owns_delivery": True,
                    "delivery_owner": "jarvis",
                    "engineering_contract_digest": engineering.digest,
                    "required_tests": [
                        list(command) for command in engineering.required_tests
                    ],
                    "base_branch": worktree.base_branch,
                    "branch_name": worktree.branch,
                    "required_checks": [],
                    "remote_identity": (
                        worktree.remote_identity.to_dict()
                        if worktree.remote_identity is not None
                        else None
                    ),
                }
            )

        # L'identifiant du run est frappé ici et **associé avant** que le
        # runtime existe. `create_and_start` programme le démarrage sans
        # l'attendre : ses premiers événements (`queued`, `resource_wait`)
        # partaient donc avant que `find_task_by_run` puisse retrouver la
        # tâche, et étaient perdus. L'association d'abord ferme cette fenêtre.
        run_id = resume_run_id or str(uuid.uuid4())
        task = self.repository.update_task(
            task_id,
            agentic_run_id=run_id,
            status=TaskStatus.QUEUED,
            current_phase="queued",
        )

        if engineering is not None and worktree is not None:
            from agents.devagent.finalizer import enqueue_engineering_finalizer

            try:
                enqueue_engineering_finalizer(
                    {
                        "job_id": worktree.job_id,
                        "run_id": run_id,
                        "repo_root": str(worktree.repo_root),
                        "worktree_path": str(worktree.workspace),
                        "branch_name": worktree.branch,
                        "base_branch": worktree.base_branch,
                        "remote_identity": (
                            worktree.remote_identity.to_dict()
                            if worktree.remote_identity is not None
                            else None
                        ),
                    },
                    required_tests=engineering.required_tests,
                    commit_message=engineering.commit_message,
                    publish_external=False,
                    required_checks=(),
                )
            except Exception as exc:
                logger.exception("finalizer non persistant pour %s", task_id)
                return await self._record_launch_failure(
                    task_id,
                    exc,
                    phase="finalizer_enqueue_failed",
                    run_id=run_id,
                )
        try:
            run = await self.agentic.create_and_start(
                run_id=run_id,
                title=task.title,
                profile_id=task.profile_id,
                origin="user",
                channel=task.source.channel.value
                if task.source.channel.value
                in {"api", "web", "voice", "mobile", "imessage", "macos"}
                else "api",
                task_id=task.task_id,
                conversation_id=task.conversation_id,
                device=str(routing.get("device") or "") or None,
                locale=str(routing.get("locale") or "fr-FR"),
                timezone_name=str(routing.get("timezone") or "Europe/Paris"),
                permissions=permissions,
                capability_profile_id=capability_profile_id,
                selected_context=selected_context,
                category=category,
                runtime_id=engineering.runtime_id if engineering is not None else None,
                workspace=worktree.workspace if worktree is not None else None,
                idempotency_key=(
                    engineering.idempotency_key if engineering is not None else None
                ),
            )
        except Exception as exc:
            logger.exception("démarrage du runtime impossible pour %s", task_id)
            if engineering is not None:
                try:
                    from agents.devagent.finalizer import (
                        fail_engineering_finalizer_launch,
                    )

                    fail_engineering_finalizer_launch(
                        engineering.job_id,
                        run_id=run_id,
                        error_code="runtime_start_failed",
                    )
                except Exception:
                    logger.warning("finalizer de lancement impossible à terminaliser")
            # Aucun run n'a été rendu : garder l'identifiant préalloué
            # laisserait la tâche pointer vers une exécution inexistante.
            return await self._record_launch_failure(
                task_id, exc, phase="start_failed"
            )

        if run.run_id != run_id:
            if engineering is not None:
                try:
                    await self.agentic.cancel(run.run_id)
                except Exception:
                    logger.warning("annulation du run divergent impossible")
                return await self._record_launch_failure(
                    task_id,
                    RuntimeError("le runtime a remplacé l'identifiant signé"),
                    phase="runtime_identity_mismatch",
                    run_id=run_id,
                )
            # Le service a rendu un autre run que celui demandé (idempotence) :
            # c'est lui qui fait foi, l'association le suit.
            task = self.repository.update_task(task_id, agentic_run_id=run.run_id)
            run_id = run.run_id

        # État **réel** du run, relu après la tentative — jamais un `running`
        # supposé. `expected_status` protège la relecture : si un événement
        # d'admission a déjà fait avancer la tâche pendant l'appel, c'est lui
        # qui fait foi et cette écriture s'efface plutôt que de le régresser.
        current = self.agentic.get(run_id) or run
        target = (
            task_status_for_run(str(getattr(current.status, "value", current.status)))
            or TaskStatus.QUEUED
        )
        if target is not TaskStatus.QUEUED:
            try:
                task = self.repository.update_task(
                    task_id,
                    expected_status=TaskStatus.QUEUED,
                    status=target,
                    current_phase=str(
                        getattr(current.status, "value", current.status)
                    ),
                )
            except (TaskPersistenceConflict, InvalidTaskTransition):
                pass
        # Relecture finale : des événements du run ont pu trancher pendant
        # l'appel. Rendre l'objet lu avant `create_and_start` ferait répondre à
        # l'API un état déjà périmé — la panne visible du rapport.
        task = self.repository.require_task(task_id)
        self.repository.append_activity(
            build_user_activity(
                task_id=task_id,
                summary="Plan validé : exécution confiée au runtime.",
                event_type=TaskActivityType.AGENT_STARTED,
            )
        )
        await self._emit(
            task,
            "task.control.started",
            run_id=run_id,
            spoken_summary="La tâche est en file d'exécution.",
        )
        return task

    # ── Synchronisation runtime ───────────────────────────────────────────

    async def on_runtime_event(
        self, event_type: str, payload: Mapping[str, Any]
    ) -> ControlTask | None:
        """Traduit un événement runtime en activité et en état de tâche."""

        run_id = str(payload.get("run_id") or "").strip()
        if not run_id:
            return None
        task = self.repository.find_task_by_run(run_id)
        if task is None:
            return None

        activity = build_activity(
            task_id=task.task_id,
            run_id=run_id,
            event_type=event_type,
            payload=payload,
        )
        if activity is not None:
            self.repository.append_activity(activity)

        status_value = str(payload.get("status") or "")
        target = task_status_for_run(status_value, event_type=event_type)
        progress = payload.get("progress")
        updates: dict[str, Any] = {}
        if isinstance(progress, (int, float)):
            updates["progress"] = max(0.0, min(1.0, float(progress)))
        phase = clamp_text(payload.get("phase") or "", 120)
        if phase:
            updates["current_phase"] = phase

        if target is None or target is task.status:
            if updates:
                task = self.repository.update_task(task.task_id, **updates)
                await self._emit(task, "task.control.progress")
            return task

        try:
            task = self.repository.update_task(
                task.task_id,
                status=target,
                attention_required=target
                in {TaskStatus.AWAITING_PERMISSION, TaskStatus.BLOCKED},
                **updates,
            )
        except InvalidTaskTransition:
            # Le run et la tâche peuvent diverger sur un rejeu ; l'état
            # persistant de la tâche gagne plutôt que de casser le flux.
            logger.debug(
                "transition %s -> %s ignorée pour %s",
                task.status.value,
                target.value,
                task.task_id,
            )
            return task

        await self._emit(task, self._event_for_status(target))
        self._notify(task)
        if target in _TERMINAL_RESULTS:
            await self.finalize(task.task_id)
        return task

    @staticmethod
    def _event_for_status(status: TaskStatus) -> str:
        return {
            TaskStatus.AWAITING_PERMISSION: "task.control.permission_required",
            TaskStatus.BLOCKED: "task.control.blocked",
            TaskStatus.COMPLETED: "task.control.completed",
            TaskStatus.FAILED: "task.control.failed",
            TaskStatus.CANCELLED: "task.control.cancelled",
        }.get(status, "task.control.progress")

    # ── Autorisations d'effet ─────────────────────────────────────────────

    def pending_approvals(self, task_id: str) -> list[dict[str, Any]]:
        """Autorisations d'effet en attente, telles que le runtime les a posées."""

        task = self.repository.require_task(task_id)
        if not task.agentic_run_id:
            return []
        approvals = self.agentic.approvals(task.agentic_run_id)
        return [
            {
                "approval_id": approval.approval_id,
                "kind": ApprovalKind.EFFECT_APPROVAL.value,
                "action": approval.action,
                "tool": approval.tool,
                "summary": approval.summary,
                "sanitized_arguments": dict(approval.sanitized_arguments),
                "risks": list(approval.risks),
                "scope": approval.scope,
                "expires_at": (
                    approval.expires_at.isoformat() if approval.expires_at else None
                ),
                # L'approbation vient d'un plugin d'exécution : un champ
                # incomplet doit dégrader l'affichage, pas casser l'écran des
                # autorisations — c'est justement celui dont on a besoin quand
                # quelque chose ne va pas.
                "decision": str(
                    getattr(approval.decision, "value", approval.decision) or "pending"
                ),
                "decision_at": (
                    approval.decision_at.isoformat() if approval.decision_at else None
                ),
            }
            for approval in approvals
        ]

    async def decide_effect_approval(
        self,
        task_id: str,
        approval_id: str,
        *,
        approved: bool,
        actor: str,
    ) -> dict[str, Any]:
        """Transmet une décision d'effet au runtime, une seule fois.

        L'unicité et l'expiration sont tenues par la couche agentique, qui
        possède déjà l'approbation ; la dupliquer ici créerait deux vérités.
        """

        from jarvis.agentic.models import ApprovalDecision

        task = self.repository.require_task(task_id)
        if not task.agentic_run_id:
            raise TaskNotFound("aucun run associé à cette tâche")
        decision = ApprovalDecision.APPROVED if approved else ApprovalDecision.DENIED
        result = await self.agentic.decide_approval(
            task.agentic_run_id,
            approval_id,
            decision=decision,
            actor=redact_text(actor, max_chars=120),
        )
        self.repository.append_activity(
            build_user_activity(
                task_id=task_id,
                summary=(
                    "Autorisation accordée." if approved else "Autorisation refusée."
                ),
                event_type=TaskActivityType.PERMISSION_DECIDED,
                run_id=task.agentic_run_id,
            )
        )
        return {
            "approval_id": approval_id,
            "decision": decision.value,
            "run_status": str(getattr(getattr(result, "status", None), "value", "")),
        }

    # ── Interventions utilisateur ─────────────────────────────────────────

    async def add_comment(
        self, task_id: str, body: str, *, author: str = "user"
    ) -> dict[str, Any]:
        """Enregistre une précision. Ne modifie jamais le plan approuvé.

        Un commentaire qui élargit le périmètre ne doit pas silencieusement
        étendre ce qui a été autorisé : il est journalisé, et l'appelant peut
        demander une révision de plan explicite.
        """

        task = self.repository.require_task(task_id)
        comment = self.repository.add_comment(
            comment_id=new_id("cmt"),
            task_id=task_id,
            body=body,
            author=author,
            run_id=task.agentic_run_id,
            plan_version=task.approved_plan_version,
        )
        self.repository.append_activity(
            build_user_activity(
                task_id=task_id,
                summary=clamp_text(body, 300),
                event_type=TaskActivityType.USER_COMMENT,
                run_id=task.agentic_run_id,
            )
        )
        return comment

    async def request_plan_revision(self, task_id: str, comment: str) -> ControlTask:
        """Force une nouvelle version de plan après un changement de périmètre.

        Une tâche en cours voit son run arrêté, mais elle n'est **pas**
        annulée : « annulée » est un verdict rendu à l'utilisateur, pas une
        étape de travail. Elle repart en planification, et la nouvelle version
        devra être approuvée comme n'importe quelle autre.
        """

        revisable = {
            TaskStatus.CREATED,
            TaskStatus.BLOCKED,
            TaskStatus.FAILED,
            TaskStatus.PLAN_REJECTED,
            TaskStatus.PLAN_REVISION_REQUESTED,
            TaskStatus.AWAITING_PLAN_APPROVAL,
        }
        async with self._lock(task_id):
            task = self.repository.require_task(task_id)
            if task.status in {
                TaskStatus.RUNNING,
                TaskStatus.AWAITING_PERMISSION,
                TaskStatus.QUEUED,
                TaskStatus.RESOURCE_WAIT,
            }:
                task = await self._abort_run_for_revision(task, comment)
            elif task.status not in revisable:
                raise InvalidTaskTransition(
                    f"révision impossible depuis {task.status.value}"
                )
            else:
                task = self.repository.update_task(
                    task_id,
                    status=TaskStatus.PLAN_REVISION_REQUESTED,
                    agentic_run_id=None,
                    approved_plan_version=None,
                    approved_plan_digest=None,
                    attention_required=False,
                )
        return await self.plan_task(task_id, revision_comment=comment)

    async def _abort_run_for_revision(
        self, task: ControlTask, comment: str
    ) -> ControlTask:
        """Arrête le run en cours sans conclure la tâche."""

        if task.agentic_run_id:
            self.repository.update_task(
                task.task_id, status=TaskStatus.CANCELLING, current_phase="revising"
            )
            try:
                await self.agentic.cancel(task.agentic_run_id)
            except Exception:
                logger.warning(
                    "arrêt du runtime impossible pour %s", task.agentic_run_id
                )
        self.repository.append_activity(
            build_user_activity(
                task_id=task.task_id,
                summary=clamp_text(f"Révision de périmètre demandée : {comment}", 300),
                event_type=TaskActivityType.DECISION_SUMMARY,
                run_id=task.agentic_run_id,
            )
        )
        return self.repository.update_task(
            task.task_id,
            status=TaskStatus.PLAN_REVISION_REQUESTED,
            agentic_run_id=None,
            approved_plan_version=None,
            approved_plan_digest=None,
            current_phase="plan_revision_requested",
            attention_required=False,
        )

    async def cancel_task(self, task_id: str, *, reason: str = "") -> ControlTask:
        """Annule. Le runtime est prévenu, mais l'état de la tâche fait foi."""

        async with self._lock(task_id):
            task = self.repository.require_task(task_id)
            if task.status in {TaskStatus.CANCELLED, TaskStatus.ARCHIVED}:
                return task
            if task.agentic_run_id:
                try:
                    self.repository.update_task(
                        task_id,
                        status=TaskStatus.CANCELLING,
                        current_phase="cancelling",
                    )
                except InvalidTaskTransition:
                    pass
                try:
                    await self.agentic.cancel(task.agentic_run_id)
                except Exception:
                    logger.warning(
                        "annulation runtime impossible pour %s", task.agentic_run_id
                    )
            try:
                task = self.repository.update_task(
                    task_id,
                    status=TaskStatus.CANCELLED,
                    current_phase="cancelled",
                    attention_required=False,
                    result_status="cancelled",
                )
            except InvalidTaskTransition as exc:
                raise InvalidTaskTransition(
                    f"annulation impossible depuis {task.status.value}"
                ) from exc
            self.repository.append_activity(
                build_user_activity(
                    task_id=task_id,
                    summary=clamp_text(reason or "Tâche annulée.", 300),
                    event_type=TaskActivityType.COMPLETED,
                )
            )
            await self._emit(task, "task.control.cancelled")
        await self.finalize(task_id, error=reason)
        return self.repository.require_task(task_id)

    # ── Rapport ───────────────────────────────────────────────────────────

    async def finalize(self, task_id: str, *, error: str = "") -> ControlTask:
        """Produit le rapport final. Une seule version par conclusion."""

        task = self.repository.require_task(task_id)
        existing = self.repository.latest_report(task_id)
        if existing is not None and existing.result_status == result_status_for(task):
            return task

        artifacts: list[Mapping[str, Any]] = []
        approvals: list[Mapping[str, Any]] = []
        verification: Mapping[str, Any] | None = None
        duration: float | None = None
        if task.agentic_run_id:
            try:
                run = self.agentic.get(task.agentic_run_id)
                artifacts = [
                    {
                        "type": item.type,
                        "reference": item.reference,
                        "sha256": item.sha256,
                        "metadata": dict(getattr(item, "metadata", {}) or {}),
                    }
                    for item in self.agentic.artifacts(task.agentic_run_id)
                ]
                approvals = [
                    {
                        "action": item.action,
                        "tool": item.tool,
                        "decision": item.decision.value,
                    }
                    for item in self.agentic.approvals(task.agentic_run_id)
                ]
                if run is not None and getattr(run, "verification", None) is not None:
                    verification = {
                        "verdict": str(getattr(run.verification.verdict, "value", "")),
                        "summary": run.verification.summary,
                    }
                if run is not None and run.started_at and run.finished_at:
                    duration = (run.finished_at - run.started_at).total_seconds()
            except Exception:
                logger.warning("collecte du rapport incomplète pour %s", task_id)

        activities = self.repository.list_activity(task_id, limit=2_000)
        report = build_report(
            task,
            version=self.repository.next_report_version(task_id),
            plan=(
                self.repository.get_plan(task_id, task.approved_plan_version)
                if task.approved_plan_version
                else None
            ),
            activities=activities,
            artifacts=artifacts,
            approvals=approvals,
            verification=verification,
            duration_s=duration,
            error=error,
        )
        self.repository.save_report(report)
        task = self.repository.update_task(
            task_id,
            final_report_id=report.report_id,
            result_status=report.result_status,
            progress=1.0 if report.result_status == "completed" else task.progress,
        )
        await self._emit(
            task,
            "task.control.completed"
            if report.result_status == "completed"
            else "task.control.failed",
            report_id=report.report_id,
            result_status=report.result_status,
            deliverable_count=len(report.data.get("deliveries", [])),
            spoken_summary=report.summary,
        )
        return task

    # ── Détection ─────────────────────────────────────────────────────────

    async def ingest_detection(
        self, detected: DetectedTask
    ) -> tuple[TaskCandidate | None, ControlTask | None]:
        """Transforme une détection en candidat ou en tâche à planifier.

        Ne répond jamais, n'exécute jamais. Le retour explicite les deux cas
        possibles plutôt que de masquer lequel s'est produit.
        """

        if not detected.is_actionable:
            return None, None
        duplicate = self.repository.find_open_task_by_dedupe(detected.source.reference)
        candidate = TaskCandidate(
            candidate_id=new_id("cand"),
            profile_id=current_profile_id(),
            suggested_title=detected.suggested_title,
            suggested_description=detected.suggested_description,
            source=detected.source,
            confidence=detected.confidence,
            reason=detected.reason,
            suggested_due_at=detected.suggested_due_at,
            dedupe_key=detected.dedupe_key,
            duplicate_of=duplicate,
        )
        candidate, created = self.repository.create_candidate(candidate)
        if not created:
            return candidate, None
        await self._emit_candidate(candidate)
        if duplicate is not None:
            return candidate, None
        if self.detector.should_create_task_directly(detected):
            task = await self.accept_candidate(candidate.candidate_id)
            return candidate, task
        self.notifications.create(
            source="task",
            title="Demande détectée",
            content=clamp_text(candidate.suggested_title, 200),
            priority="low",
        )
        return candidate, None

    async def accept_candidate(self, candidate_id: str) -> ControlTask:
        candidate = self.repository.get_candidate(candidate_id)
        if candidate is None:
            raise TaskNotFound(candidate_id)
        if candidate.created_task_id:
            return self.repository.require_task(candidate.created_task_id)
        task = await self.create_task(
            title=candidate.suggested_title,
            description=candidate.suggested_description,
            source=candidate.source,
            due_at=candidate.suggested_due_at,
            autoplan=True,
        )
        self.repository.decide_candidate(
            candidate_id,
            decision=CandidateDecision.ACCEPTED,
            created_task_id=task.task_id,
        )
        return self.repository.require_task(task.task_id)

    def decide_candidate(
        self,
        candidate_id: str,
        *,
        decision: CandidateDecision | str,
        merge_into: str | None = None,
    ) -> TaskCandidate:
        wanted = CandidateDecision(decision)
        if wanted is CandidateDecision.ACCEPTED:
            raise ValueError("utiliser accept_candidate pour créer la tâche")
        return self.repository.decide_candidate(
            candidate_id, decision=wanted, duplicate_of=merge_into
        )

    # ── Lecture ───────────────────────────────────────────────────────────

    def list_tasks(
        self,
        *,
        statuses: Iterable[TaskStatus | str] | None = None,
        attention_only: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> list[ControlTask]:
        return self.repository.list_tasks(
            statuses=statuses,
            attention_only=attention_only,
            limit=limit,
            offset=offset,
        )

    def activity(
        self,
        task_id: str,
        *,
        after_sequence: int = 0,
        levels: Iterable[TaskActivityLevel | str] | None = None,
        limit: int = 500,
    ) -> list[TaskActivity]:
        return self.repository.list_activity(
            task_id, after_sequence=after_sequence, levels=levels, limit=limit
        )

    def artifacts(self, task_id: str) -> list[dict[str, Any]]:
        task = self.repository.require_task(task_id)
        if not task.agentic_run_id:
            return []
        return [
            {
                "artifact_id": item.artifact_id,
                "type": item.type,
                "reference": item.reference,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
            }
            for item in self.agentic.artifacts(task.agentic_run_id)
        ]

    # ── Sorties ───────────────────────────────────────────────────────────

    async def _emit(self, task: ControlTask, event_type: str, **extra: Any) -> None:
        payload: dict[str, Any] = {
            "task_id": task.task_id,
            "status": task.status.value,
            "phase": task.current_phase,
            "title": task.title,
            "priority": task.priority.value,
            "progress": task.progress,
            "source_type": task.source.source_type.value,
            "source_channel": task.source.channel.value,
            "needs_attention": task.needs_attention,
            "run_id": task.agentic_run_id or "",
            "result_status": task.result_status or "",
        }
        payload.update(extra)
        try:
            await emit_task_control_event(event_type, payload, bus=self.bus)
        except Exception:
            logger.exception("émission d'événement de tâche impossible")

    async def _emit_candidate(self, candidate: TaskCandidate) -> None:
        try:
            await emit_task_control_event(
                "task.control.candidate_detected",
                {
                    "candidate_id": candidate.candidate_id,
                    "task_id": candidate.candidate_id,
                    "title": candidate.suggested_title,
                    "confidence": candidate.confidence,
                    "source_type": candidate.source.source_type.value,
                    "source_channel": candidate.source.channel.value,
                    "status": "candidate",
                    "needs_attention": True,
                },
                bus=self.bus,
            )
        except Exception:
            logger.exception("émission de candidat impossible")

    def _notify(self, task: ControlTask) -> None:
        entry = _NOTIFICATION_BY_STATUS.get(task.status)
        if entry is None:
            return
        title, content, priority = entry
        try:
            self.notifications.create(
                source="task",
                title=title,
                content=f"{content} — « {clamp_text(task.title, 120)} »",
                priority=priority,
            )
        except Exception:
            logger.exception("notification de tâche impossible")


_service: TaskControlService | None = None


def get_task_control_service() -> TaskControlService:
    global _service
    if _service is None:
        _service = TaskControlService()
    return _service


def reset_task_control_service_for_tests() -> None:
    global _service
    _service = None


__all__ = [
    "TaskControlService",
    "get_task_control_service",
    "reset_task_control_service_for_tests",
]

"""Orchestrateur générique des runtimes agentiques."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from typing import Any
import uuid

from database.agentic import (
    AgenticRepository,
    AgenticRunNotFound,
    ApprovalAlreadyDecided,
    ApprovalExpired,
)
from database.core import (
    DEFAULT_PROFILE_ID,
    current_profile_id,
    normalize_profile_id,
    use_profile,
)
from database.profiles import list_user_profiles, user_profile_exists
from jarvis.event_bus import AGENTIC_EVENT_TYPES, EventBus, event_bus
from jarvis.notification_service import NotificationService, notification_service

from .context import build_agentic_context, build_run_budget
from .events import emit_agentic_event
from .models import (
    AgenticError,
    AgenticErrorCode,
    AgenticRequestCategory,
    AgenticRun,
    AgenticRunStatus,
    ApprovalDecision,
    ApprovalRequest,
    Artifact,
    RunBudget,
    RuntimeEvent,
    RuntimeHealthStatus,
    TERMINAL_RUN_STATUSES,
    VerificationResult,
    VerificationVerdict,
    validate_run_transition,
)
from .profiles import (
    CAPABILITY_PROFILE_CONTEXT_KEY,
    capability_profile_id_from_context,
    get_capability_profile,
    select_capability_profile,
)
from .redaction import neutralize_event_payload, redact_mapping, redact_text
from .registry import RuntimePluginError, RuntimeRegistry
from .verifier import (
    CompletionVerifier,
    DEFAULT_RUNTIME_VERIFIER,
    DEFAULT_VERIFIER_REGISTRY,
    VerifierRegistry,
    build_jarvis_receipt_artifact,
)


logger = logging.getLogger(__name__)

_APPROVAL_DEFAULT_TTL = timedelta(minutes=10)
_APPROVAL_MAX_TTL = timedelta(minutes=15)
_DEFAULT_APPROVAL_RISK = "Action sensible soumise à confirmation utilisateur."


_STATUS_EVENTS: dict[AgenticRunStatus, str] = {
    AgenticRunStatus.CLASSIFIED: "agent.run.classified",
    AgenticRunStatus.QUEUED: "agent.run.queued",
    AgenticRunStatus.PROVISIONING: "agent.run.provisioning",
    AgenticRunStatus.PLANNING: "agent.run.phase_changed",
    AgenticRunStatus.AWAITING_APPROVAL: "agent.run.awaiting_approval",
    AgenticRunStatus.RUNNING: "agent.run.started",
    AgenticRunStatus.VERIFYING: "agent.run.verifying",
    AgenticRunStatus.REVIEWING: "agent.run.reviewing",
    AgenticRunStatus.PAUSED: "agent.run.paused",
    AgenticRunStatus.BLOCKED: "agent.run.blocked",
    AgenticRunStatus.CANCELLING: "agent.run.cancelling",
    AgenticRunStatus.CANCELLED: "agent.run.cancelled",
    AgenticRunStatus.FAILED: "agent.run.failed",
    AgenticRunStatus.COMPLETED: "agent.run.completed",
    AgenticRunStatus.EXPIRED: "agent.run.expired",
    AgenticRunStatus.PROVIDER_UNAVAILABLE: "agent.run.provider_unavailable",
}

_NOTIFICATION_BY_STATUS: dict[AgenticRunStatus, tuple[str, str, str]] = {
    AgenticRunStatus.BLOCKED: (
        "Run agentique bloqué",
        "Le run attend une intervention avant de pouvoir continuer.",
        "high",
    ),
    AgenticRunStatus.CANCELLED: (
        "Run agentique annulé",
        "Le run a été annulé.",
        "medium",
    ),
    AgenticRunStatus.COMPLETED: (
        "Run agentique terminé",
        "Le résultat a été vérifié et le run est terminé.",
        "medium",
    ),
    AgenticRunStatus.EXPIRED: (
        "Run agentique expiré",
        "Le run a atteint sa limite de temps.",
        "high",
    ),
    AgenticRunStatus.FAILED: (
        "Échec du run agentique",
        "Le run s’est arrêté avec une erreur.",
        "high",
    ),
    AgenticRunStatus.PROVIDER_UNAVAILABLE: (
        "Runtime agentique indisponible",
        "Le run ne peut pas continuer tant que son runtime est indisponible.",
        "high",
    ),
}

_APPROVAL_NOTIFICATION = (
    "Approbation agentique requise",
    "Une action sensible attend votre décision dans JARVIS.",
    "high",
)
_APPROVAL_EXPIRED_NOTIFICATION = (
    "Approbation agentique expirée",
    "Le délai de confirmation est dépassé ; le run attend votre intervention.",
    "high",
)
_EVENT_PROCESSING_LEASE_SECONDS = 60
_APPROVAL_SWEEP_INTERVAL_SECONDS = 15.0


def _maintenance_profile_ids() -> tuple[str, ...]:
    """Retourne les profils actifs dont la base isolée est encore autorisée."""

    profile_ids: list[str] = []
    seen: set[str] = set()
    rows = list_user_profiles()
    for row in rows:
        raw_profile_id = row.get("id") if isinstance(row, Mapping) else None
        profile_id = normalize_profile_id(raw_profile_id)
        if profile_id in seen:
            continue
        seen.add(profile_id)
        if not user_profile_exists(profile_id):
            logger.warning(
                "maintenance agentique ignorée pour un profil sans base autorisée: %s",
                profile_id,
            )
            continue
        profile_ids.append(profile_id)
    if DEFAULT_PROFILE_ID not in seen:
        profile_ids.insert(0, DEFAULT_PROFILE_ID)
    return tuple(profile_ids)


def _capability_profile_routing_config() -> tuple[str, Mapping[str, str]]:
    """Charge la politique centrale sans rendre le core dépendant d'un plugin."""

    try:
        import config
    except ImportError:
        return "readonly-research", {}
    default_profile = str(
        getattr(config, "AGENTIC_DEFAULT_PROFILE", "readonly-research")
    )
    configured_routes = getattr(config, "AGENTIC_PROFILE_ROUTE_OVERRIDES", {})
    if not isinstance(configured_routes, Mapping):
        configured_routes = {}
    return default_profile, configured_routes


class AgenticService:
    """Source de vérité des états, indépendamment de l'état observé du runtime."""

    def __init__(
        self,
        *,
        repository: AgenticRepository | None = None,
        registry: RuntimeRegistry | None = None,
        bus: EventBus | None = None,
        verifier: CompletionVerifier | None = None,
        verifier_registry: VerifierRegistry | None = None,
        notifications: NotificationService | None = None,
    ) -> None:
        self.repository = repository or AgenticRepository()
        self.registry = registry or RuntimeRegistry()
        self.bus = bus or event_bus
        self.verifier = verifier or DEFAULT_RUNTIME_VERIFIER
        self.verifier_registry = verifier_registry or DEFAULT_VERIFIER_REGISTRY
        self.notifications = (
            notifications if notifications is not None else notification_service
        )
        self._admission_lock = asyncio.Lock()
        self._run_locks: dict[str, asyncio.Lock] = {}
        self._start_tasks: dict[str, asyncio.Task[AgenticRun]] = {}
        self._event_tasks: dict[str, asyncio.Task[None]] = {}
        self._terminal_events: dict[str, asyncio.Event] = {}
        self._maintenance_task: asyncio.Task[None] | None = None

    def _lock(self, run_id: str) -> asyncio.Lock:
        return self._run_locks.setdefault(run_id, asyncio.Lock())

    def _terminal_event(self, run_id: str) -> asyncio.Event:
        return self._terminal_events.setdefault(run_id, asyncio.Event())

    def _schedule_start(self, run: AgenticRun) -> None:
        current = self._start_tasks.get(run.run_id)
        if current is not None and not current.done():
            return
        with use_profile(run.profile_id):
            self._start_tasks[run.run_id] = asyncio.create_task(
                self.start_run(run.run_id),
                name=f"agentic-start:{run.run_id}",
            )

    def _schedule_queued_runs(self, profile_id: str) -> None:
        with use_profile(profile_id):
            queued = self.repository.list_runs(
                statuses=(AgenticRunStatus.QUEUED,),
                limit=100,
            )
        for run in queued:
            self._schedule_start(run)

    def resolve_runtime_id(self, requested: str | None = None) -> str | None:
        """Résout ``auto`` vers le premier plugin activé, sans nom fournisseur."""

        selected = requested
        if selected is None:
            try:
                import config

                selected = str(getattr(config, "AGENTIC_RUNTIME", "auto"))
            except ImportError:
                selected = "auto"
        normalized = (selected or "auto").strip()
        if normalized.casefold() == "auto":
            manifest = next(iter(self.registry.manifests), None)
            return manifest.runtime_id if manifest is not None else None
        return normalized or None

    async def _record_and_emit(
        self,
        run: AgenticRun,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        external_event_id: str | None = None,
        event_id: str | None = None,
    ) -> RuntimeEvent:
        extra = dict(payload or {})
        safe_payload = neutralize_event_payload(
            {
                **extra,
                "run_id": run.run_id,
                "status": run.status.value,
                "phase": run.phase,
                "channel": run.channel,
                "title": run.title,
                "progress": extra.get(
                    "progress",
                    1.0 if run.status is AgenticRunStatus.COMPLETED else 0.0,
                ),
                "needs_attention": extra.get(
                    "needs_attention",
                    run.status
                    in {
                        AgenticRunStatus.AWAITING_APPROVAL,
                        AgenticRunStatus.BLOCKED,
                        AgenticRunStatus.FAILED,
                        AgenticRunStatus.PROVIDER_UNAVAILABLE,
                    },
                ),
                "spoken_summary": extra.get("spoken_summary", ""),
            }
        )
        runtime_event = RuntimeEvent(
            event_id=event_id or str(uuid.uuid4()),
            run_id=run.run_id,
            sequence=0,
            type=event_type,
            timestamp=datetime.now(timezone.utc),
            payload=safe_payload,
            external_event_id=external_event_id,
        )
        with use_profile(run.profile_id):
            stored, created = self.repository.append_event(runtime_event)
            if created:
                await emit_agentic_event(
                    event_type,
                    stored.payload,
                    bus=self.bus,
                    source="jarvis.agentic.service",
                )
                self._notify_for_event(run, stored)
        return stored

    def _notify_for_event(self, run: AgenticRun, event: RuntimeEvent) -> None:
        """Publie une notification sans exposer prompt ni données du runtime."""

        if event.type == "agent.approval.requested":
            title, content, priority = _APPROVAL_NOTIFICATION
            approval_id = str(event.payload.get("approval_id") or event.event_id)
            notification_key = f"approval:{run.run_id}:{approval_id}"
        elif (
            event.type == "agent.approval.resolved"
            and event.payload.get("decision") == ApprovalDecision.EXPIRED.value
        ):
            title, content, priority = _APPROVAL_EXPIRED_NOTIFICATION
            approval_id = str(event.payload.get("approval_id") or event.event_id)
            notification_key = f"approval-expired:{run.run_id}:{approval_id}"
        else:
            notification_spec = _NOTIFICATION_BY_STATUS.get(run.status)
            if notification_spec is None or event.type != _STATUS_EVENTS.get(
                run.status
            ):
                return
            title, content, priority = notification_spec
            notification_key = f"status:{run.run_id}:{event.event_id}"

        try:
            self.notifications.create(
                source="agentic",
                title=title,
                content=content,
                priority=priority,
                idempotency_key=(f"{run.profile_id}:{notification_key}"),
            )
        except Exception as exc:
            logger.warning(
                "notification agentique indisponible pour %s (%s)",
                run.run_id,
                type(exc).__name__,
            )

    async def _transition(
        self,
        run: AgenticRun,
        status: AgenticRunStatus,
        *,
        phase: str | None = None,
        event_type: str | None = None,
        error: AgenticError | None = None,
        verification: VerificationResult | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> AgenticRun:
        with use_profile(run.profile_id):
            updated = self.repository.transition_run(
                run.run_id,
                status,
                phase=phase,
                error=error,
                verification=verification,
            )
            try:
                self.repository.record_metric(
                    run_id=updated.run_id,
                    metric=f"run.status.{updated.status.value}",
                    value=1.0,
                    unit="count",
                    metadata={
                        "runtime_id": updated.runtime_id,
                        "phase": updated.phase,
                        "error_code": updated.error.code.value
                        if updated.error
                        else None,
                    },
                )
                if (
                    updated.status in TERMINAL_RUN_STATUSES
                    and updated.started_at is not None
                    and updated.finished_at is not None
                ):
                    self.repository.record_metric(
                        run_id=updated.run_id,
                        metric="run.duration",
                        value=max(
                            0.0,
                            (updated.finished_at - updated.started_at).total_seconds(),
                        ),
                        unit="seconds",
                        metadata={"status": updated.status.value},
                    )
            except Exception:
                logger.warning(
                    "agentic_metric_failed run_id=%s runtime_id=%s phase=%s",
                    updated.run_id,
                    updated.runtime_id,
                    updated.phase,
                )
        logger.info(
            "agentic_transition run_id=%s runtime_id=%s phase=%s event_type=%s error_code=%s",
            updated.run_id,
            updated.runtime_id,
            updated.phase,
            event_type or _STATUS_EVENTS[updated.status],
            updated.error.code.value if updated.error else "",
        )
        await self._record_and_emit(
            updated,
            event_type or _STATUS_EVENTS[updated.status],
            payload,
        )
        if updated.status in TERMINAL_RUN_STATUSES:
            self._terminal_event(updated.run_id).set()
            self._schedule_queued_runs(updated.profile_id)
        return updated

    async def create_run(
        self,
        *,
        title: str,
        runtime_id: str | None = None,
        profile_id: str | None = None,
        origin: str = "user",
        channel: str = "api",
        task_id: str | None = None,
        conversation_id: str | None = None,
        device: str | None = None,
        locale: str = "fr-FR",
        timezone_name: str = "Europe/Paris",
        permissions: tuple[str, ...] | list[str] = (),
        capability_profile_id: str | None = None,
        selected_context: Mapping[str, Any] | None = None,
        category: AgenticRequestCategory | str = AgenticRequestCategory.DIRECT_ACTION,
        budget: RunBudget | None = None,
        workspace: str | Path | None = None,
        idempotency_key: str | None = None,
        run_id: str | None = None,
    ) -> AgenticRun:
        selected_profile = normalize_profile_id(profile_id or current_profile_id())
        if selected_profile != current_profile_id():
            raise PermissionError("création de run cross-profile interdite")
        context_payload = dict(selected_context or {})
        context_payload.setdefault("request", redact_text(title, max_chars=8000))
        selected_category = AgenticRequestCategory(category)
        default_capability_profile, route_overrides = (
            _capability_profile_routing_config()
        )
        profile_request = str(context_payload.get("request") or title)
        capability_profile = select_capability_profile(
            profile_request,
            selected_category,
            default_profile_id=default_capability_profile,
            route_overrides=(
                {selected_category.value: capability_profile_id}
                if capability_profile_id is not None
                else route_overrides
            ),
        )
        requested_permissions = tuple(dict.fromkeys(permissions))
        refused_by_profile = capability_profile.refused_permissions(
            requested_permissions
        )
        if capability_profile_id is not None and refused_by_profile:
            raise PermissionError("permission hors du profil de capacités JARVIS")
        # La décision du routeur écrase toujours un éventuel marqueur fourni par
        # l'appelant. Le runtime ne peut donc ni choisir ni élargir son profil.
        context_payload[CAPABILITY_PROFILE_CONTEXT_KEY] = capability_profile.profile_id
        selected_runtime = self.resolve_runtime_id(runtime_id) or "unavailable"
        run = AgenticRun(
            run_id=run_id or str(uuid.uuid4()),
            profile_id=selected_profile,
            origin=origin,
            channel=channel,
            runtime_id=selected_runtime,
            title=redact_text(title, max_chars=240),
            task_id=task_id,
            conversation_id=conversation_id,
            device=device,
            locale=locale,
            timezone=timezone_name,
            permissions=requested_permissions,
            selected_context=redact_mapping(context_payload),
            category=selected_category,
            budget=budget or build_run_budget(),
            workspace=str(workspace) if workspace is not None else None,
            idempotency_key=idempotency_key,
        )
        stored, created = self.repository.create_run(run)
        if created:
            await self._record_and_emit(stored, "agent.run.created")
        return stored

    async def start_run(self, run_id: str) -> AgenticRun:
        async with self._lock(run_id):
            run = self.repository.require_run(run_id)
            if run.terminal:
                return run
            if run.status is AgenticRunStatus.CREATED:
                run = await self._transition(run, AgenticRunStatus.CLASSIFIED)
            if run.status is AgenticRunStatus.CLASSIFIED:
                run = await self._transition(run, AgenticRunStatus.QUEUED)
            if run.status not in {AgenticRunStatus.QUEUED, AgenticRunStatus.BLOCKED}:
                return run
            try:
                capability_profile_id = capability_profile_id_from_context(
                    run.selected_context
                )
                refused_by_profile = (
                    ()
                    if capability_profile_id is None
                    else get_capability_profile(
                        capability_profile_id
                    ).refused_permissions(run.permissions)
                )
            except ValueError:
                refused_by_profile = ("invalid_profile",)
            if refused_by_profile:
                error = AgenticError(
                    AgenticErrorCode.PERMISSION_DENIED,
                    "capacité hors du profil JARVIS sélectionné",
                    details={"refused_count": len(refused_by_profile)},
                )
                return await self._transition(
                    run,
                    AgenticRunStatus.FAILED,
                    error=error,
                    payload={"error_code": error.code.value, "needs_attention": True},
                )
            if run.origin == "agent_runtime":
                error = AgenticError(
                    AgenticErrorCode.INVALID_REQUEST,
                    "un appel runtime doit contourner la reclassification agentique",
                )
                return await self._transition(
                    run,
                    AgenticRunStatus.FAILED,
                    error=error,
                    payload={"error_code": error.code.value, "needs_attention": True},
                )
            runtime = None
            runtime_load_failed = False
            try:
                runtime = await self.registry.get(run.runtime_id)
            except Exception as exc:
                runtime_load_failed = True
                logger.warning(
                    "runtime %s non chargeable (%s)",
                    run.runtime_id,
                    type(exc).__name__,
                )
            if runtime is None:
                error = AgenticError(
                    (
                        AgenticErrorCode.RUNTIME_UNAVAILABLE
                        if runtime_load_failed
                        or self.registry.manifest(run.runtime_id) is not None
                        else AgenticErrorCode.RUNTIME_NOT_FOUND
                    ),
                    "runtime agentique indisponible",
                    retryable=True,
                )
                return await self._transition(
                    run,
                    AgenticRunStatus.PROVIDER_UNAVAILABLE,
                    error=error,
                    payload={"error_code": error.code.value, "needs_attention": True},
                )
            try:
                health = await runtime.health()
            except Exception:
                logger.warning("health runtime %s indisponible", run.runtime_id)
                health = None
            if (
                health is None
                or getattr(health, "status", None) is RuntimeHealthStatus.UNAVAILABLE
                or getattr(health, "status", None) not in set(RuntimeHealthStatus)
            ):
                error = AgenticError(
                    AgenticErrorCode.RUNTIME_UNAVAILABLE,
                    "runtime agentique non sain",
                    retryable=True,
                )
                return await self._transition(
                    run,
                    AgenticRunStatus.PROVIDER_UNAVAILABLE,
                    error=error,
                    payload={"error_code": error.code.value, "needs_attention": True},
                )
            allowed_permissions = {
                value
                for capability in runtime.capabilities
                for value in (capability.name, capability.scope)
            }
            refused_permissions = sorted(set(run.permissions) - allowed_permissions)
            if refused_permissions:
                error = AgenticError(
                    AgenticErrorCode.PERMISSION_DENIED,
                    "capacité non déclarée par le runtime",
                    details={"refused_count": len(refused_permissions)},
                )
                return await self._transition(
                    run,
                    AgenticRunStatus.FAILED,
                    error=error,
                    payload={"error_code": error.code.value, "needs_attention": True},
                )
            active_statuses = (
                AgenticRunStatus.PROVISIONING,
                AgenticRunStatus.PLANNING,
                AgenticRunStatus.AWAITING_APPROVAL,
                AgenticRunStatus.RUNNING,
                AgenticRunStatus.VERIFYING,
                AgenticRunStatus.REVIEWING,
            )
            async with self._admission_lock:
                with use_profile(run.profile_id):
                    run = self.repository.require_run(run.run_id)
                    active_runs = self.repository.list_runs(
                        statuses=active_statuses,
                        limit=1000,
                    )
                effective_limit = min(
                    (run.budget.concurrency_limit,)
                    + tuple(item.budget.concurrency_limit for item in active_runs)
                )
                if len(active_runs) >= effective_limit:
                    return run
                run = await self._transition(run, AgenticRunStatus.PROVISIONING)
            if run.budget.deadline is not None and run.budget.deadline <= datetime.now(
                timezone.utc
            ):
                return await self._transition(
                    run,
                    AgenticRunStatus.EXPIRED,
                    error=AgenticError(
                        AgenticErrorCode.BUDGET_EXCEEDED,
                        "deadline du run dépassée avant démarrage",
                    ),
                    payload={"error_code": AgenticErrorCode.BUDGET_EXCEEDED.value},
                )
            try:
                context = build_agentic_context(
                    run_id=run.run_id,
                    profile_id=run.profile_id,
                    conversation_id=run.conversation_id,
                    task_id=run.task_id,
                    channel=run.channel,
                    device=run.device,
                    locale=run.locale,
                    timezone_name=run.timezone,
                    permissions=run.permissions,
                    selected_context=run.selected_context,
                    origin=run.origin,
                )
            except ValueError:
                error = AgenticError(
                    AgenticErrorCode.INVALID_REQUEST,
                    "contexte agentique refusé",
                )
                return await self._transition(
                    run,
                    AgenticRunStatus.FAILED,
                    error=error,
                    payload={"error_code": error.code.value, "needs_attention": True},
                )
            try:
                provider_session_id = await runtime.create_run(run, context)
                with use_profile(run.profile_id):
                    run = self.repository.set_provider_session(
                        run.run_id, provider_session_id
                    )
                await runtime.start(run)
            except Exception as exc:
                logger.warning(
                    "échec de démarrage du runtime %s (%s)",
                    run.runtime_id,
                    type(exc).__name__,
                )
                error = AgenticError(
                    AgenticErrorCode.RUNTIME_UNAVAILABLE,
                    redact_text(exc, max_chars=500),
                    retryable=True,
                )
                return await self._transition(
                    run,
                    AgenticRunStatus.FAILED,
                    error=error,
                    payload={"error_code": error.code.value, "needs_attention": True},
                )
            run = await self._transition(run, AgenticRunStatus.RUNNING)
            self._start_event_stream(run, runtime)
            return run

    def _start_event_stream(self, run: AgenticRun, runtime: Any) -> None:
        current = self._event_tasks.get(run.run_id)
        if current is not None and not current.done():
            return
        self._event_tasks[run.run_id] = asyncio.create_task(
            self._consume_runtime_events(run, runtime),
            name=f"agentic-events:{run.run_id}",
        )

    @staticmethod
    def _approval_event_payload(approval: ApprovalRequest) -> dict[str, Any]:
        return {
            "approval_id": approval.approval_id,
            "action": approval.action,
            "tool": approval.tool,
            "sanitized_arguments": dict(approval.sanitized_arguments),
            "risks": list(approval.risks),
            "expires_at": approval.expires_at.isoformat()
            if approval.expires_at
            else None,
            "needs_attention": True,
            "spoken_summary": approval.summary,
        }

    async def _apply_persisted_runtime_event(
        self,
        run: AgenticRun,
        event: RuntimeEvent,
    ) -> None:
        """Applique les effets d'un événement durable de façon rejouable."""

        current = self.repository.require_run(run.run_id)
        if event.type == "agent.approval.requested":
            approval_id = str(event.payload.get("approval_id") or "")
            if not approval_id:
                if (
                    not current.terminal
                    and current.status is not AgenticRunStatus.BLOCKED
                ):
                    error = AgenticError(
                        AgenticErrorCode.RUNTIME_PROTOCOL,
                        "le runtime a demandé une approbation sans identifiant",
                    )
                    await self._transition(
                        current,
                        AgenticRunStatus.BLOCKED,
                        error=error,
                        payload={
                            "error_code": error.code.value,
                            "needs_attention": True,
                        },
                    )
                return

            existing = self.repository.get_approval(approval_id)
            if existing is None:
                raw_arguments = event.payload.get("sanitized_arguments")
                if not isinstance(raw_arguments, Mapping):
                    raw_arguments = event.payload.get("arguments")
                if not isinstance(raw_arguments, Mapping):
                    raw_arguments = {}
                raw_risks = event.payload.get("risks")
                if not isinstance(raw_risks, (list, tuple)):
                    raw_risks = ()
                await self.request_approval(
                    ApprovalRequest(
                        approval_id=approval_id,
                        run_id=run.run_id,
                        action=redact_text(
                            event.payload.get("action") or "Action sensible",
                            max_chars=240,
                        ),
                        tool=redact_text(
                            event.payload.get("tool") or "runtime.tool",
                            max_chars=120,
                        ),
                        summary=redact_text(
                            event.payload.get("spoken_summary")
                            or event.payload.get("title")
                            or "Approbation utilisateur requise",
                            max_chars=1000,
                        ),
                        sanitized_arguments=redact_mapping(raw_arguments),
                        risks=tuple(
                            redact_text(item, max_chars=240) for item in raw_risks[:20]
                        ),
                    ),
                    emit_event=False,
                )
                return

            current = self.repository.require_run(run.run_id)
            if (
                existing.decision is ApprovalDecision.PENDING
                and not current.terminal
                and current.status is not AgenticRunStatus.AWAITING_APPROVAL
            ):
                await self._transition(
                    current,
                    AgenticRunStatus.AWAITING_APPROVAL,
                    payload=self._approval_event_payload(existing),
                )
            return

        runtime_phase = str(event.payload.get("phase") or "")
        if (
            event.type == "agent.run.phase_changed"
            and runtime_phase == "runtime_completed"
        ):
            if current.terminal:
                return
            if current.status in {
                AgenticRunStatus.RUNNING,
                AgenticRunStatus.PLANNING,
            }:
                current = await self._transition(
                    current,
                    AgenticRunStatus.VERIFYING,
                    event_type="agent.run.verifying",
                )
            if (
                bool(current.selected_context.get("jarvis_owns_delivery"))
                and current.status is AgenticRunStatus.VERIFYING
            ):
                try:
                    await self.refresh_artifacts(current.run_id)
                except Exception:
                    logger.exception(
                        "collecte durable des artefacts impossible pour %s",
                        current.run_id,
                    )
                    error = AgenticError(
                        AgenticErrorCode.RUNTIME_PROTOCOL,
                        "les artefacts du runtime n'ont pas pu être persistés",
                    )
                    await self._transition(
                        current,
                        AgenticRunStatus.BLOCKED,
                        error=error,
                        payload={
                            "error_code": error.code.value,
                            "needs_attention": True,
                            "spoken_summary": error.message,
                        },
                    )
                else:
                    await self._transition(
                        current,
                        AgenticRunStatus.REVIEWING,
                        phase="awaiting_jarvis_validation",
                        payload={
                            "spoken_summary": "En attente des validations JARVIS.",
                            "progress": 0.9,
                        },
                    )
                return
            if current.status in {
                AgenticRunStatus.VERIFYING,
                AgenticRunStatus.REVIEWING,
            }:
                await self.verify_run(current.run_id)
            return

        if (
            event.type == "agent.run.phase_changed"
            and runtime_phase == "runtime_failed"
        ):
            if current.terminal:
                return
            raw_code = str(event.payload.get("error_code") or "").strip().lower()
            if raw_code == AgenticErrorCode.BUDGET_EXCEEDED.value:
                error = AgenticError(
                    AgenticErrorCode.BUDGET_EXCEEDED,
                    "le runtime a franchi une limite de budget",
                )
            else:
                error = AgenticError(
                    AgenticErrorCode.RUNTIME_PROTOCOL,
                    "le runtime a signalé un échec",
                )
            await self._transition(
                current,
                AgenticRunStatus.FAILED,
                error=error,
                payload={
                    "error_code": error.code.value,
                    "violation": event.payload.get("violation"),
                },
            )

    async def _process_persisted_runtime_event(
        self,
        run: AgenticRun,
        event: RuntimeEvent,
    ) -> bool:
        """Réserve, applique puis acquitte une entrée d'inbox durable."""

        with use_profile(run.profile_id):
            claim_token = self.repository.claim_event_processing(
                run.run_id,
                event.event_id,
                lease_seconds=_EVENT_PROCESSING_LEASE_SECONDS,
            )
            if claim_token is None:
                return False
            try:
                current = self.repository.require_run(run.run_id)
                await emit_agentic_event(
                    event.type,
                    event.payload,
                    bus=self.bus,
                    source="jarvis.agentic.runtime",
                )
                self._notify_for_event(current, event)
                await self._apply_persisted_runtime_event(current, event)
            except asyncio.CancelledError:
                self.repository.release_event_processing(
                    run.run_id,
                    event.event_id,
                    claim_token,
                    error="traitement annulé",
                )
                raise
            except Exception as exc:
                self.repository.release_event_processing(
                    run.run_id,
                    event.event_id,
                    claim_token,
                    error=redact_text(exc, max_chars=500),
                )
                raise
            return self.repository.complete_event_processing(
                run.run_id,
                event.event_id,
                claim_token,
            )

    async def _consume_runtime_events(self, run: AgenticRun, runtime: Any) -> None:
        try:
            async for incoming in runtime.stream_events(run.run_id):
                if incoming.run_id != run.run_id:
                    logger.error("événement cross-run refusé pour %s", run.run_id)
                    continue
                if incoming.type not in AGENTIC_EVENT_TYPES:
                    logger.warning(
                        "événement runtime inconnu refusé: %s", incoming.type
                    )
                    continue
                with use_profile(run.profile_id):
                    current = self.repository.get_run(run.run_id)
                if current is None or current.terminal:
                    return
                provider_terminal = incoming.type in {
                    "agent.run.completed",
                    "agent.run.failed",
                }
                observed = replace(
                    incoming,
                    # La séquence runtime est propre au fournisseur. JARVIS
                    # alloue sa séquence canonique atomiquement en base.
                    sequence=0,
                    payload={
                        **incoming.payload,
                        "run_id": current.run_id,
                        "status": current.status.value,
                        "phase": current.phase,
                        "channel": current.channel,
                        "title": current.title,
                        "progress": incoming.payload.get("progress", 0.0),
                        "needs_attention": incoming.payload.get(
                            "needs_attention", False
                        ),
                        "spoken_summary": incoming.payload.get("spoken_summary", ""),
                    },
                )
                if provider_terminal:
                    observed = RuntimeEvent(
                        event_id=observed.event_id,
                        run_id=observed.run_id,
                        sequence=observed.sequence,
                        type="agent.run.phase_changed",
                        timestamp=observed.timestamp,
                        payload={
                            **observed.payload,
                            "phase": "runtime_completed"
                            if incoming.type == "agent.run.completed"
                            else "runtime_failed",
                            "status": current.status.value,
                        },
                        level=observed.level,
                        visibility=observed.visibility,
                        sensitivity=observed.sensitivity,
                        external_event_id=observed.external_event_id,
                    )
                with use_profile(run.profile_id):
                    stored, _created = self.repository.append_event(
                        observed,
                        requires_processing=True,
                    )
                await self._process_persisted_runtime_event(run, stored)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "flux d'événements agentique interrompu (%s)",
                type(exc).__name__,
            )
            with use_profile(run.profile_id):
                current = self.repository.get_run(run.run_id)
            if current is not None and not current.terminal:
                error = AgenticError(
                    AgenticErrorCode.RUNTIME_PROTOCOL,
                    redact_text(exc, max_chars=500),
                    retryable=True,
                )
                await self._transition(
                    current,
                    AgenticRunStatus.BLOCKED,
                    error=error,
                    payload={"error_code": error.code.value, "needs_attention": True},
                )

    async def create_and_start(
        self,
        *,
        title: str,
        runtime_id: str | None = None,
        profile_id: str | None = None,
        origin: str = "user",
        channel: str = "api",
        task_id: str | None = None,
        conversation_id: str | None = None,
        device: str | None = None,
        locale: str = "fr-FR",
        timezone_name: str = "Europe/Paris",
        permissions: tuple[str, ...] | list[str] = (),
        capability_profile_id: str | None = None,
        selected_context: Mapping[str, Any] | None = None,
        category: AgenticRequestCategory | str = AgenticRequestCategory.DIRECT_ACTION,
        budget: RunBudget | None = None,
        workspace: str | Path | None = None,
        idempotency_key: str | None = None,
        run_id: str | None = None,
    ) -> AgenticRun:
        """Persiste immédiatement puis programme le démarrage sans attendre le run."""

        run = await self.create_run(
            title=title,
            runtime_id=runtime_id,
            profile_id=profile_id,
            origin=origin,
            channel=channel,
            task_id=task_id,
            conversation_id=conversation_id,
            device=device,
            locale=locale,
            timezone_name=timezone_name,
            permissions=permissions,
            capability_profile_id=capability_profile_id,
            selected_context=selected_context,
            category=category,
            budget=budget,
            workspace=workspace,
            idempotency_key=idempotency_key,
            run_id=run_id,
        )
        if run.status in {
            AgenticRunStatus.CREATED,
            AgenticRunStatus.CLASSIFIED,
            AgenticRunStatus.QUEUED,
        }:
            self._schedule_start(run)
        return run

    def get(self, run_id: str) -> AgenticRun | None:
        return self.repository.get_run(run_id)

    def list(
        self,
        *,
        statuses: tuple[AgenticRunStatus, ...] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgenticRun]:
        return self.repository.list_runs(statuses=statuses, limit=limit, offset=offset)

    async def pause(self, run_id: str) -> AgenticRun:
        async with self._lock(run_id):
            run = self.repository.require_run(run_id)
            validate_run_transition(run.status, AgenticRunStatus.PAUSED)
            runtime = await self.registry.get(run.runtime_id)
            if runtime is None:
                raise RuntimePluginError("runtime indisponible")
            await runtime.pause(run_id)
            return await self._transition(run, AgenticRunStatus.PAUSED)

    async def resume(self, run_id: str) -> AgenticRun:
        async with self._lock(run_id):
            run = self.repository.require_run(run_id)
            validate_run_transition(run.status, AgenticRunStatus.RUNNING)
            runtime = await self.registry.get(run.runtime_id)
            if runtime is None:
                raise RuntimePluginError("runtime indisponible")
            await runtime.resume(run_id)
            updated = await self._transition(
                run,
                AgenticRunStatus.RUNNING,
                event_type="agent.run.resumed",
            )
            self._start_event_stream(updated, runtime)
            return updated

    async def cancel(self, run_id: str) -> AgenticRun:
        async with self._lock(run_id):
            run = self.repository.require_run(run_id)
            if run.terminal:
                return run
            cancellation_requires_runtime = run.status not in {
                AgenticRunStatus.CREATED,
                AgenticRunStatus.CLASSIFIED,
                AgenticRunStatus.QUEUED,
            }
            run = await self._transition(run, AgenticRunStatus.CANCELLING)
            runtime = None
            try:
                runtime = await self.registry.get(run.runtime_id)
                if runtime is None and cancellation_requires_runtime:
                    error = AgenticError(
                        AgenticErrorCode.RUNTIME_UNAVAILABLE,
                        "annulation non confirmée : runtime indisponible",
                        retryable=True,
                    )
                    return await self._transition(
                        run,
                        AgenticRunStatus.FAILED,
                        error=error,
                        payload={
                            "error_code": error.code.value,
                            "needs_attention": True,
                        },
                    )
                if runtime is not None and cancellation_requires_runtime:
                    await runtime.cancel(run_id)
            except Exception as exc:
                error = AgenticError(
                    AgenticErrorCode.RUNTIME_UNAVAILABLE,
                    redact_text(exc, max_chars=500),
                    retryable=True,
                )
                return await self._transition(
                    run,
                    AgenticRunStatus.FAILED,
                    error=error,
                    payload={"error_code": error.code.value, "needs_attention": True},
                )
            finally:
                task = self._event_tasks.pop(run_id, None)
                if task is not None and task is not asyncio.current_task():
                    task.cancel()
            return await self._transition(run, AgenticRunStatus.CANCELLED)

    async def request_approval(
        self,
        approval: ApprovalRequest,
        *,
        emit_event: bool = True,
    ) -> ApprovalRequest:
        async with self._lock(approval.run_id):
            run = self.repository.require_run(approval.run_id)
            if run.status is not AgenticRunStatus.AWAITING_APPROVAL:
                validate_run_transition(run.status, AgenticRunStatus.AWAITING_APPROVAL)
            now = datetime.now(timezone.utc)
            expires_at = approval.expires_at
            if expires_at is None:
                expires_at = now + _APPROVAL_DEFAULT_TTL
            elif expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            else:
                expires_at = expires_at.astimezone(timezone.utc)
            if expires_at <= now:
                raise ValueError("une approbation doit expirer dans le futur")
            expires_at = min(expires_at, now + _APPROVAL_MAX_TTL)
            safe = replace(
                approval,
                action=redact_text(approval.action, max_chars=240),
                tool=redact_text(approval.tool, max_chars=120),
                summary=redact_text(approval.summary, max_chars=1000),
                sanitized_arguments=redact_mapping(approval.sanitized_arguments),
                risks=tuple(
                    redact_text(item, max_chars=240)
                    for item in (approval.risks or (_DEFAULT_APPROVAL_RISK,))
                ),
                expires_at=expires_at,
            )
            stored = self.repository.create_approval(safe)
            approval_payload = {
                "approval_id": stored.approval_id,
                "action": stored.action,
                "tool": stored.tool,
                "sanitized_arguments": dict(stored.sanitized_arguments),
                "risks": list(stored.risks),
                "expires_at": stored.expires_at.isoformat()
                if stored.expires_at
                else None,
                "needs_attention": True,
                "spoken_summary": stored.summary,
            }
            if emit_event:
                await self._record_and_emit(
                    run,
                    "agent.approval.requested",
                    approval_payload,
                    external_event_id=f"approval:{stored.approval_id}:requested",
                )
            if run.status is not AgenticRunStatus.AWAITING_APPROVAL:
                await self._transition(
                    run,
                    AgenticRunStatus.AWAITING_APPROVAL,
                    payload=approval_payload,
                )
            return stored

    async def _record_approval_expirations(
        self,
        run: AgenticRun,
        *,
        now: datetime | None = None,
        transition_when_empty: bool,
    ) -> tuple[list[ApprovalRequest], list[ApprovalRequest], AgenticRun]:
        """Expire, publie et répare les effets d'une expiration rejouable."""

        self.repository.expire_due_approval_requests(run.run_id, now=now)
        approvals = self.repository.list_approvals(run.run_id)
        pending = [
            approval
            for approval in approvals
            if approval.decision is ApprovalDecision.PENDING
        ]
        expired = [
            approval
            for approval in approvals
            if approval.decision is ApprovalDecision.EXPIRED
        ]
        for approval in expired:
            await self._record_and_emit(
                run,
                "agent.approval.resolved",
                {
                    "approval_id": approval.approval_id,
                    "action": approval.action,
                    "tool": approval.tool,
                    "decision": ApprovalDecision.EXPIRED.value,
                    "needs_attention": True,
                    "spoken_summary": "Le délai d’approbation est dépassé.",
                },
                external_event_id=(f"approval:{approval.approval_id}:resolved:expired"),
            )
        latest = self.repository.require_run(run.run_id)
        if (
            transition_when_empty
            and expired
            and not pending
            and latest.status is AgenticRunStatus.AWAITING_APPROVAL
        ):
            error = AgenticError(
                AgenticErrorCode.APPROVAL_EXPIRED,
                "le délai d'approbation est dépassé",
            )
            latest = await self._transition(
                latest,
                AgenticRunStatus.BLOCKED,
                error=error,
                payload={
                    "error_code": error.code.value,
                    "needs_attention": True,
                },
            )
        return approvals, pending, latest

    async def decide_approval(
        self,
        run_id: str,
        approval_id: str,
        decision: ApprovalDecision | str,
        *,
        decided_by: str,
        decision_id: str,
    ) -> ApprovalRequest:
        async with self._lock(run_id):
            run = self.repository.require_run(run_id)
            _approvals, _pending, run = await self._record_approval_expirations(
                run,
                transition_when_empty=True,
            )
            approval = self.repository.get_approval(approval_id)
            if approval is None or approval.run_id != run_id:
                raise AgenticRunNotFound(f"approval {approval_id}")
            if approval.decision is ApprovalDecision.EXPIRED:
                raise ApprovalExpired(f"approval expiré: {approval_id}")
            decided = self.repository.decide_approval(
                approval_id,
                decision,
                decided_by=decided_by,
                decision_id=decision_id,
            )
            delivery_claimed_at = datetime.now(timezone.utc)
            claimed = self.repository.claim_approval_delivery(
                approval_id,
                now=delivery_claimed_at,
            )
            delivery_status = self.repository.approval_delivery_status(approval_id)
            if claimed:
                try:
                    runtime = await self.registry.get(run.runtime_id)
                    if (
                        runtime is None
                        and self.registry.manifest(run.runtime_id) is not None
                    ):
                        raise RuntimePluginError(
                            "runtime indisponible pour livrer l'approbation"
                        )
                    if runtime is not None:
                        await runtime.answer_approval(run_id, decided)
                except Exception as exc:
                    self.repository.release_approval_delivery(
                        approval_id,
                        error=redact_text(exc, max_chars=500),
                        claim_started_at=delivery_claimed_at,
                    )
                    if isinstance(exc, RuntimePluginError):
                        raise
                    raise RuntimePluginError(
                        "livraison de l'approbation au runtime impossible"
                    ) from exc
                self.repository.complete_approval_delivery(
                    approval_id,
                    claim_started_at=delivery_claimed_at,
                )
            elif delivery_status != "delivered":
                raise RuntimePluginError("livraison de l'approbation déjà en cours")

            approvals, pending, run = await self._record_approval_expirations(
                self.repository.require_run(run_id),
                transition_when_empty=False,
            )
            rejected = any(
                item.decision in {ApprovalDecision.DENIED, ApprovalDecision.EXPIRED}
                for item in approvals
            )
            await self._record_and_emit(
                run,
                "agent.approval.resolved",
                {
                    "approval_id": decided.approval_id,
                    "action": decided.action,
                    "tool": decided.tool,
                    "needs_attention": bool(pending),
                },
                external_event_id=(
                    f"approval:{decided.approval_id}:resolved:{decided.decision.value}"
                ),
            )
            if run.status is AgenticRunStatus.AWAITING_APPROVAL and not pending:
                target = (
                    AgenticRunStatus.BLOCKED if rejected else AgenticRunStatus.RUNNING
                )
                await self._transition(
                    run,
                    target,
                    event_type=(
                        "agent.run.resumed"
                        if target is AgenticRunStatus.RUNNING
                        else "agent.run.blocked"
                    ),
                )
            return decided

    async def verify_run(
        self,
        run_id: str,
        *,
        verifier: CompletionVerifier | None = None,
    ) -> AgenticRun:
        """Collecte les preuves puis applique un verdict JARVIS déterministe.

        Le contrat générique est toujours exécuté en premier. Un vérificateur
        spécialisé peut renforcer ce verdict, jamais contourner un échec ou un
        blocage du contrat runtime/artefacts.
        """

        run = self.repository.require_run(run_id)
        if run.status not in {
            AgenticRunStatus.VERIFYING,
            AgenticRunStatus.REVIEWING,
        }:
            raise ValueError("le run doit être en vérification ou revue")

        runtime = None
        collection_error_code: str | None = None
        try:
            runtime = await self.registry.get(run.runtime_id)
        except Exception:
            collection_error_code = AgenticErrorCode.RUNTIME_UNAVAILABLE.value
        if runtime is None and collection_error_code is None:
            collection_error_code = (
                AgenticErrorCode.RUNTIME_UNAVAILABLE.value
                if self.registry.manifest(run.runtime_id) is not None
                else AgenticErrorCode.RUNTIME_NOT_FOUND.value
            )

        collected: tuple[object, ...] = ()
        if runtime is not None:
            try:
                collected = tuple(await runtime.get_artifacts(run_id))
            except Exception:
                logger.warning(
                    "collecte des artefacts impossible pour le run %s", run_id
                )
                collection_error_code = AgenticErrorCode.RUNTIME_PROTOCOL.value

        trusted_artifacts = tuple(self.repository.list_artifacts(run_id))
        trusted_ids = frozenset(artifact.artifact_id for artifact in trusted_artifacts)
        cached_runtime_artifacts = tuple(
            artifact
            for artifact in trusted_artifacts
            if artifact.type not in {"jarvis_test_receipt", "jarvis_effect_receipt"}
        )
        if collection_error_code is not None and cached_runtime_artifacts:
            # ``refresh_artifacts`` persiste le manifeste au passage en revue afin
            # qu'un redémarrage du provider ne rende pas la vérification impossible.
            collection_error_code = None
        collected = tuple(
            artifact
            for artifact in collected
            if not isinstance(artifact, Artifact)
            or artifact.artifact_id not in trusted_ids
        )
        verification_artifacts = (*trusted_artifacts, *collected)
        trusted_artifact_ids = frozenset(
            artifact.artifact_id
            for artifact in trusted_artifacts
            if artifact.type in {"jarvis_test_receipt", "jarvis_effect_receipt"}
        )
        baseline = DEFAULT_RUNTIME_VERIFIER.verify(
            run=run,
            artifacts=verification_artifacts,
            collection_error_code=collection_error_code,
            trusted_artifact_ids=trusted_artifact_ids,
        )
        result = baseline
        selected_verifier = verifier or self.verifier
        if selected_verifier is DEFAULT_RUNTIME_VERIFIER:
            selected_verifier = self.verifier_registry.resolve(
                run=run,
                artifacts=verification_artifacts,
            )
        if (
            baseline.verdict is VerificationVerdict.PASS
            and selected_verifier is not DEFAULT_RUNTIME_VERIFIER
        ):
            try:
                candidate = selected_verifier.verify(
                    run=run,
                    artifacts=verification_artifacts,
                    collection_error_code=None,
                )
                if not isinstance(candidate, VerificationResult):
                    raise TypeError("verdict de vérification invalide")
                result = candidate
            except Exception:
                logger.warning("vérificateur spécialisé en échec pour %s", run_id)
                result = DEFAULT_RUNTIME_VERIFIER.verify(
                    run=run,
                    artifacts=(),
                    collection_error_code="verifier_error",
                )

        if baseline.verdict is VerificationVerdict.PASS:
            for artifact in collected:
                # Le baseline garantit le type et l'appartenance au run.
                assert isinstance(artifact, Artifact)
                with use_profile(run.profile_id):
                    stored, created = self.repository.add_artifact(artifact)
                if created:
                    await self._record_and_emit(
                        run,
                        "agent.artifact.created",
                        {"artifact_id": stored.artifact_id},
                    )

        latest = self.repository.require_run(run_id)
        if latest.terminal:
            return latest
        return await self.apply_verification_result(run_id, result)

    async def apply_verification_result(
        self,
        run_id: str,
        result: VerificationResult,
    ) -> AgenticRun:
        """Applique le verdict JARVIS ; le runtime ne peut pas s'auto-valider."""

        async with self._lock(run_id):
            run = self.repository.require_run(run_id)
            if run.status not in {
                AgenticRunStatus.VERIFYING,
                AgenticRunStatus.REVIEWING,
            }:
                raise ValueError("le run doit être en vérification ou revue")
            if result.verdict is VerificationVerdict.PASS:
                if bool(run.selected_context.get("jarvis_owns_delivery")):
                    persisted = tuple(self.repository.list_artifacts(run_id))
                    trusted_receipts = frozenset(
                        artifact.artifact_id
                        for artifact in persisted
                        if artifact.type
                        in {"jarvis_test_receipt", "jarvis_effect_receipt"}
                    )
                    delivery_gate = DEFAULT_RUNTIME_VERIFIER.verify(
                        run=run,
                        artifacts=persisted,
                        trusted_artifact_ids=trusted_receipts,
                    )
                    if delivery_gate.verdict is not VerificationVerdict.PASS:
                        raise ValueError(
                            "completed exige un reçu de tests JARVIS vérifié"
                        )
                spoken_summary = result.summary
                for artifact in reversed(self.artifacts(run_id)):
                    candidate = artifact.metadata.get("voice_summary")
                    if artifact.type == "runtime_result" and isinstance(candidate, str):
                        candidate = redact_text(candidate, max_chars=280).strip()
                        if candidate:
                            spoken_summary = candidate
                            break
                return await self._transition(
                    run,
                    AgenticRunStatus.COMPLETED,
                    verification=result,
                    payload={"spoken_summary": spoken_summary, "progress": 1.0},
                )
            if result.verdict is VerificationVerdict.BLOCKED:
                return await self._transition(
                    run,
                    AgenticRunStatus.BLOCKED,
                    verification=result,
                    payload={"spoken_summary": result.summary, "needs_attention": True},
                )
            error = AgenticError(
                AgenticErrorCode.VERIFICATION_FAILED,
                result.summary,
            )
            return await self._transition(
                run,
                AgenticRunStatus.FAILED,
                verification=result,
                error=error,
                payload={
                    "spoken_summary": result.summary,
                    "error_code": error.code.value,
                    "needs_attention": True,
                },
            )

    def events(self, run_id: str, *, after_sequence: int = 0) -> list[RuntimeEvent]:
        return self.repository.list_events(run_id, after_sequence=after_sequence)

    def artifacts(self, run_id: str) -> list[Artifact]:
        return self.repository.list_artifacts(run_id)

    async def record_verification_receipt(
        self,
        run_id: str,
        *,
        kind: str,
        subject: str,
        details: Mapping[str, Any] | None = None,
        artifact_id: str | None = None,
    ) -> Artifact:
        """Persiste une observation JARVIS, distincte des affirmations du runtime."""

        run = self.repository.require_run(run_id)
        if artifact_id is not None:
            existing = next(
                (
                    item
                    for item in self.repository.list_artifacts(run_id)
                    if item.artifact_id == artifact_id
                ),
                None,
            )
            expected_type = f"jarvis_{kind.strip().lower()}_receipt"
            if existing is not None:
                if existing.type != expected_type or existing.run_id != run_id:
                    raise RuntimeError("identifiant de reçu JARVIS déjà utilisé")
                return existing
        receipt = build_jarvis_receipt_artifact(
            run_id=run_id,
            kind=kind,
            subject=redact_text(subject, max_chars=500),
            details=redact_mapping(details),
            artifact_id=artifact_id,
        )
        with use_profile(run.profile_id):
            stored, created = self.repository.add_artifact(receipt)
        if created:
            await self._record_and_emit(
                run,
                "agent.artifact.created",
                {"artifact_id": stored.artifact_id},
            )
        return stored

    async def fail_jarvis_delivery(
        self,
        run_id: str,
        *,
        error_code: str,
        summary: str,
    ) -> AgenticRun:
        """Ferme durablement une remise dont les gates JARVIS ont échoué."""

        async with self._lock(run_id):
            run = self.repository.require_run(run_id)
            if run.terminal:
                return run
            if run.status not in {
                AgenticRunStatus.VERIFYING,
                AgenticRunStatus.REVIEWING,
            }:
                raise ValueError("le run n'attend pas une remise JARVIS")
            safe_code = redact_text(error_code or "validation_failed", max_chars=120)
            safe_summary = redact_text(
                summary or "validation JARVIS échouée", max_chars=1000
            )
            error = AgenticError(
                AgenticErrorCode.VERIFICATION_FAILED,
                safe_summary,
                details={"delivery_error_code": safe_code},
            )
            return await self._transition(
                run,
                AgenticRunStatus.FAILED,
                error=error,
                payload={
                    "error_code": safe_code,
                    "spoken_summary": safe_summary,
                    "needs_attention": True,
                },
            )

    def approvals(self, run_id: str) -> list[ApprovalRequest]:
        return self.repository.list_approvals(run_id)

    async def refresh_artifacts(self, run_id: str) -> list[Artifact]:
        run = self.repository.require_run(run_id)
        runtime = await self.registry.get(run.runtime_id)
        if runtime is None:
            return self.artifacts(run_id)
        collected = tuple(await runtime.get_artifacts(run_id))
        identifiers: set[str] = set()
        known_bytes = 0
        for artifact in collected:
            if not isinstance(artifact, Artifact) or artifact.run_id != run_id:
                raise RuntimePluginError("artefact runtime hors contrat")
            if artifact.artifact_id in identifiers:
                raise RuntimePluginError("identifiant d'artefact dupliqué")
            identifiers.add(artifact.artifact_id)
            known_bytes += artifact.size_bytes or 0
        if known_bytes > run.budget.max_artifact_bytes:
            raise RuntimePluginError("budget d'artefacts dépassé")
        for artifact in collected:
            with use_profile(run.profile_id):
                stored, created = self.repository.add_artifact(artifact)
            if created:
                await self._record_and_emit(
                    run,
                    "agent.artifact.created",
                    {"artifact_id": stored.artifact_id},
                )
        return self.artifacts(run_id)

    async def runtime_status(self) -> list[dict[str, Any]]:
        runs = self.list(limit=10_000)
        queued_statuses = {
            AgenticRunStatus.CREATED,
            AgenticRunStatus.CLASSIFIED,
            AgenticRunStatus.QUEUED,
            AgenticRunStatus.PROVISIONING,
        }
        active_statuses = {
            AgenticRunStatus.PLANNING,
            AgenticRunStatus.AWAITING_APPROVAL,
            AgenticRunStatus.RUNNING,
            AgenticRunStatus.VERIFYING,
            AgenticRunStatus.REVIEWING,
            AgenticRunStatus.PAUSED,
            AgenticRunStatus.CANCELLING,
        }

        def _counts(runtime_id: str) -> tuple[int, int]:
            matching = (run for run in runs if run.runtime_id == runtime_id)
            queued = 0
            active = 0
            for run in matching:
                queued += run.status in queued_statuses
                active += run.status in active_statuses
            return active, queued

        statuses: list[dict[str, Any]] = []
        for manifest in self.registry.manifests:
            active_runs, queued_runs = _counts(manifest.runtime_id)
            try:
                runtime = await self.registry.get(manifest.runtime_id)
                health = await runtime.health() if runtime is not None else None
                health_status = (
                    health.status
                    if health is not None
                    else RuntimeHealthStatus.UNAVAILABLE
                )
                statuses.append(
                    {
                        "runtime_id": manifest.runtime_id,
                        "name": manifest.name,
                        "label": manifest.name,
                        "mode": manifest.runtime_id,
                        "version": manifest.version,
                        "status": health_status.value,
                        "available": health_status
                        in {RuntimeHealthStatus.HEALTHY, RuntimeHealthStatus.DEGRADED},
                        "active_runs": active_runs,
                        "queued_runs": queued_runs,
                        "checked_at": (
                            health.checked_at.isoformat()
                            if health is not None
                            else datetime.now(timezone.utc).isoformat()
                        ),
                        "error_code": (
                            None
                            if health_status is not RuntimeHealthStatus.UNAVAILABLE
                            else "runtime_unavailable"
                        ),
                        "capabilities": [item.name for item in manifest.capabilities],
                    }
                )
            except Exception:
                statuses.append(
                    {
                        "runtime_id": manifest.runtime_id,
                        "name": manifest.name,
                        "label": manifest.name,
                        "mode": manifest.runtime_id,
                        "version": manifest.version,
                        "status": RuntimeHealthStatus.UNAVAILABLE.value,
                        "available": False,
                        "active_runs": active_runs,
                        "queued_runs": queued_runs,
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                        "error_code": "runtime_unavailable",
                        "capabilities": [item.name for item in manifest.capabilities],
                    }
                )
        return statuses

    def observability_summary(self) -> dict[str, Any]:
        return self.repository.observability_summary()

    async def replay_unprocessed_runtime_events(self, *, limit: int = 1000) -> int:
        """Reprend les inbox de chaque profil actif dans sa base isolée."""

        processed = 0
        bounded_limit = max(1, min(int(limit), 1000))
        for profile_id in _maintenance_profile_ids():
            remaining = bounded_limit - processed
            if remaining <= 0:
                break
            with use_profile(profile_id):
                events = self.repository.list_unprocessed_events(
                    profile_id=profile_id,
                    limit=remaining,
                )
                for event in events:
                    run = self.repository.get_run(
                        event.run_id,
                        profile_id=profile_id,
                    )
                    if run is None:
                        continue
                    try:
                        if await self._process_persisted_runtime_event(run, event):
                            processed += 1
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(
                            "replay de l'événement runtime %s impossible (%s)",
                            event.event_id,
                            type(exc).__name__,
                        )
        return processed

    async def sweep_expired_approvals(
        self,
        *,
        now: datetime | None = None,
    ) -> list[ApprovalRequest]:
        """Expire les TTL et répare événements, notifications et état du run."""

        expired: list[ApprovalRequest] = []
        for profile_id in _maintenance_profile_ids():
            with use_profile(profile_id):
                snapshots = self.repository.list_nonterminal(profile_id=profile_id)
                for snapshot in snapshots:
                    if snapshot.status not in {
                        AgenticRunStatus.AWAITING_APPROVAL,
                        AgenticRunStatus.BLOCKED,
                    }:
                        continue
                    async with self._lock(snapshot.run_id):
                        run = self.repository.get_run(
                            snapshot.run_id,
                            profile_id=profile_id,
                        )
                        if run is None or run.terminal:
                            continue
                        already_expired = {
                            approval.approval_id
                            for approval in self.repository.list_approvals(run.run_id)
                            if approval.decision is ApprovalDecision.EXPIRED
                        }
                        (
                            approvals,
                            _pending,
                            _latest,
                        ) = await self._record_approval_expirations(
                            run,
                            now=now,
                            transition_when_empty=True,
                        )
                        expired.extend(
                            approval
                            for approval in approvals
                            if approval.decision is ApprovalDecision.EXPIRED
                            and approval.approval_id not in already_expired
                        )
        return expired

    async def _maintenance_loop(self) -> None:
        while True:
            await asyncio.sleep(_APPROVAL_SWEEP_INTERVAL_SECONDS)
            try:
                await self.replay_unprocessed_runtime_events()
                await self.sweep_expired_approvals()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "maintenance agentique interrompue pour un cycle (%s)",
                    type(exc).__name__,
                )

    def start_maintenance(self) -> None:
        """Démarre le sweeper/replayer périodique une seule fois."""

        if self._maintenance_task is not None and not self._maintenance_task.done():
            return
        self._maintenance_task = asyncio.create_task(
            self._maintenance_loop(),
            name="agentic-maintenance",
        )

    async def _reconcile_profile_nonterminal(
        self,
        profile_id: str,
    ) -> list[AgenticRun]:
        """Réconcilie une base profil déjà activée, sans effet externe rejoué."""

        reconciled: list[AgenticRun] = []
        for run in self.repository.list_nonterminal(profile_id=profile_id):
            if run.status in {
                AgenticRunStatus.CREATED,
                AgenticRunStatus.CLASSIFIED,
                AgenticRunStatus.QUEUED,
            }:
                reconciled.append(run)
                continue
            if run.status in {
                AgenticRunStatus.PAUSED,
                AgenticRunStatus.BLOCKED,
                AgenticRunStatus.AWAITING_APPROVAL,
            }:
                reconciled.append(run)
                continue
            if run.status is AgenticRunStatus.REVIEWING and bool(
                run.selected_context.get("jarvis_owns_delivery")
            ):
                # Les artefacts sont déjà persistés avant cette phase. Le worker
                # JARVIS peut donc reprendre les gates sans réanimer le provider.
                reconciled.append(run)
                continue
            if run.status is AgenticRunStatus.CANCELLING:
                run = await self._transition(
                    run,
                    AgenticRunStatus.FAILED,
                    error=AgenticError(
                        AgenticErrorCode.RUNTIME_UNAVAILABLE,
                        "annulation non confirmée après redémarrage",
                    ),
                )
                reconciled.append(run)
                continue
            try:
                runtime = await self.registry.get(run.runtime_id)
            except Exception:
                runtime = None
            if runtime is None:
                run = await self._transition(
                    run,
                    AgenticRunStatus.PROVIDER_UNAVAILABLE,
                    error=AgenticError(
                        AgenticErrorCode.RUNTIME_UNAVAILABLE,
                        "runtime absent pendant la réconciliation",
                        retryable=True,
                    ),
                )
                reconciled.append(run)
                continue
            try:
                health = await runtime.health()
            except Exception:
                logger.warning(
                    "health runtime indisponible pendant la réconciliation de %s",
                    run.run_id,
                )
                health = None
            if health is None or health.status is RuntimeHealthStatus.UNAVAILABLE:
                run = await self._transition(
                    run,
                    AgenticRunStatus.PROVIDER_UNAVAILABLE,
                    error=AgenticError(
                        AgenticErrorCode.RUNTIME_UNAVAILABLE,
                        "runtime indisponible pendant la réconciliation",
                        retryable=True,
                    ),
                )
            elif run.status not in {
                AgenticRunStatus.PAUSED,
                AgenticRunStatus.BLOCKED,
                AgenticRunStatus.AWAITING_APPROVAL,
            }:
                run = await self._transition(
                    run,
                    AgenticRunStatus.BLOCKED,
                    error=AgenticError(
                        AgenticErrorCode.RUNTIME_PROTOCOL,
                        "reprise explicite requise après redémarrage",
                        retryable=True,
                    ),
                    payload={"needs_attention": True},
                )
            reconciled.append(run)
        return reconciled

    async def reconcile_nonterminal(self) -> list[AgenticRun]:
        """Réconcilie chaque profil actif dans sa propre base isolée."""

        await self.replay_unprocessed_runtime_events()
        await self.sweep_expired_approvals()
        reconciled: list[AgenticRun] = []
        for profile_id in _maintenance_profile_ids():
            with use_profile(profile_id):
                reconciled.extend(await self._reconcile_profile_nonterminal(profile_id))
        return reconciled

    async def wait_for_jarvis_delivery(
        self,
        run_id: str,
        timeout: float | None = None,
    ) -> AgenticRun:
        """Attend la revue JARVIS ou un état terminal sans créer de deadlock."""

        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + max(0.0, timeout)
        while True:
            run = self.repository.require_run(run_id)
            if run.status is AgenticRunStatus.REVIEWING or run.terminal:
                return run
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                raise TimeoutError(
                    f"run non prêt pour la remise après {timeout}s: {run_id}"
                )
            await asyncio.sleep(0.25 if remaining is None else min(0.25, remaining))

    async def wait_for_terminal(
        self,
        run_id: str,
        timeout: float | None = None,
    ) -> AgenticRun:
        """Attend un état terminal par observation, sans rejouer le run."""

        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + max(0.0, timeout)
        terminal_event = self._terminal_event(run_id)
        while True:
            run = self.repository.require_run(run_id)
            if run.terminal:
                terminal_event.set()
                return run
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                raise TimeoutError(f"run non terminal après {timeout}s: {run_id}")
            window = 0.25 if remaining is None else min(0.25, remaining)
            try:
                await asyncio.wait_for(terminal_event.wait(), timeout=window)
            except asyncio.TimeoutError:
                continue

    async def dispose(self) -> None:
        tasks: tuple[asyncio.Task[Any], ...] = tuple(
            {
                *self._start_tasks.values(),
                *self._event_tasks.values(),
                *(
                    (self._maintenance_task,)
                    if self._maintenance_task is not None
                    else ()
                ),
            }
        )
        self._start_tasks.clear()
        self._event_tasks.clear()
        self._maintenance_task = None
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.registry.dispose()


_agentic_service: AgenticService | None = None


def get_agentic_service() -> AgenticService:
    global _agentic_service
    if _agentic_service is None:
        _agentic_service = AgenticService()
    return _agentic_service


async def reset_agentic_service_for_tests() -> None:
    global _agentic_service
    service = _agentic_service
    _agentic_service = None
    if service is not None:
        await service.dispose()


__all__ = [
    "AgenticService",
    "ApprovalAlreadyDecided",
    "get_agentic_service",
    "reset_agentic_service_for_tests",
]

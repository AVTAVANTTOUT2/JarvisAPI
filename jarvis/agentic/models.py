"""Contrats de domaine du moteur agentique JARVIS.

Ce module reste volontairement indépendant de tout runtime concret. Les valeurs
persistées et exposées aux canaux JARVIS utilisent exclusivement ce vocabulaire
générique.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_DEFAULT_AGENTIC_LOCALE = "fr-FR"
_DEFAULT_AGENTIC_TIMEZONE = "Europe/Paris"
_AGENTIC_LOCALE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}$")


def utc_now() -> datetime:
    """Retourne un horodatage UTC conscient du fuseau."""

    return datetime.now(timezone.utc)


def _frozen_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


def normalize_agentic_client_context(
    *,
    device: object = None,
    locale: object = None,
    timezone_name: object = None,
) -> tuple[str | None, str, str]:
    """Borne le contexte client non fiable avant persistance dans un run."""

    normalized_device: str | None = None
    if isinstance(device, str):
        candidate = device.strip()
        if (
            candidate
            and len(candidate) <= 128
            and all(ord(char) >= 32 and ord(char) != 127 for char in candidate)
        ):
            normalized_device = candidate

    normalized_locale = _DEFAULT_AGENTIC_LOCALE
    if isinstance(locale, str):
        candidate = locale.strip()
        if len(candidate) <= 32 and _AGENTIC_LOCALE_RE.fullmatch(candidate):
            normalized_locale = candidate

    normalized_timezone = _DEFAULT_AGENTIC_TIMEZONE
    if isinstance(timezone_name, str):
        candidate = timezone_name.strip()
        if candidate and len(candidate) <= 64:
            try:
                ZoneInfo(candidate)
            except (ValueError, ZoneInfoNotFoundError):
                pass
            else:
                normalized_timezone = candidate

    return normalized_device, normalized_locale, normalized_timezone


def _canonical_json_value(value: Any) -> Any:
    """Normalise les valeurs persistables avant un digest d'idempotence."""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("valeur flottante non canonique")
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_canonical_json_value(item) for item in value]
    raise ValueError(f"valeur non canonique: {type(value).__name__}")


class AgenticRunStatus(str, Enum):
    CREATED = "created"
    CLASSIFIED = "classified"
    QUEUED = "queued"
    PROVISIONING = "provisioning"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    PAUSED = "paused"
    BLOCKED = "blocked"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"
    COMPLETED = "completed"
    EXPIRED = "expired"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


TERMINAL_RUN_STATUSES: frozenset[AgenticRunStatus] = frozenset(
    {
        AgenticRunStatus.CANCELLED,
        AgenticRunStatus.FAILED,
        AgenticRunStatus.COMPLETED,
        AgenticRunStatus.EXPIRED,
        AgenticRunStatus.PROVIDER_UNAVAILABLE,
    }
)


ALLOWED_RUN_TRANSITIONS: Mapping[AgenticRunStatus, frozenset[AgenticRunStatus]] = {
    AgenticRunStatus.CREATED: frozenset(
        {
            AgenticRunStatus.CLASSIFIED,
            AgenticRunStatus.QUEUED,
            AgenticRunStatus.CANCELLING,
            AgenticRunStatus.CANCELLED,
            AgenticRunStatus.FAILED,
            AgenticRunStatus.EXPIRED,
            AgenticRunStatus.PROVIDER_UNAVAILABLE,
        }
    ),
    AgenticRunStatus.CLASSIFIED: frozenset(
        {
            AgenticRunStatus.QUEUED,
            AgenticRunStatus.CANCELLING,
            AgenticRunStatus.CANCELLED,
            AgenticRunStatus.FAILED,
            AgenticRunStatus.EXPIRED,
            AgenticRunStatus.PROVIDER_UNAVAILABLE,
        }
    ),
    AgenticRunStatus.QUEUED: frozenset(
        {
            AgenticRunStatus.PROVISIONING,
            AgenticRunStatus.CANCELLING,
            AgenticRunStatus.CANCELLED,
            AgenticRunStatus.FAILED,
            AgenticRunStatus.EXPIRED,
            AgenticRunStatus.PROVIDER_UNAVAILABLE,
        }
    ),
    AgenticRunStatus.PROVISIONING: frozenset(
        {
            AgenticRunStatus.PLANNING,
            AgenticRunStatus.RUNNING,
            AgenticRunStatus.PAUSED,
            AgenticRunStatus.BLOCKED,
            AgenticRunStatus.CANCELLING,
            AgenticRunStatus.FAILED,
            AgenticRunStatus.EXPIRED,
            AgenticRunStatus.PROVIDER_UNAVAILABLE,
        }
    ),
    AgenticRunStatus.PLANNING: frozenset(
        {
            AgenticRunStatus.AWAITING_APPROVAL,
            AgenticRunStatus.RUNNING,
            AgenticRunStatus.VERIFYING,
            AgenticRunStatus.PAUSED,
            AgenticRunStatus.BLOCKED,
            AgenticRunStatus.CANCELLING,
            AgenticRunStatus.FAILED,
            AgenticRunStatus.EXPIRED,
            AgenticRunStatus.PROVIDER_UNAVAILABLE,
        }
    ),
    AgenticRunStatus.AWAITING_APPROVAL: frozenset(
        {
            AgenticRunStatus.PLANNING,
            AgenticRunStatus.RUNNING,
            AgenticRunStatus.PAUSED,
            AgenticRunStatus.BLOCKED,
            AgenticRunStatus.CANCELLING,
            AgenticRunStatus.CANCELLED,
            AgenticRunStatus.FAILED,
            AgenticRunStatus.EXPIRED,
            AgenticRunStatus.PROVIDER_UNAVAILABLE,
        }
    ),
    AgenticRunStatus.RUNNING: frozenset(
        {
            AgenticRunStatus.PLANNING,
            AgenticRunStatus.AWAITING_APPROVAL,
            AgenticRunStatus.VERIFYING,
            AgenticRunStatus.PAUSED,
            AgenticRunStatus.BLOCKED,
            AgenticRunStatus.CANCELLING,
            AgenticRunStatus.FAILED,
            AgenticRunStatus.EXPIRED,
            AgenticRunStatus.PROVIDER_UNAVAILABLE,
        }
    ),
    AgenticRunStatus.VERIFYING: frozenset(
        {
            AgenticRunStatus.REVIEWING,
            AgenticRunStatus.RUNNING,
            AgenticRunStatus.AWAITING_APPROVAL,
            AgenticRunStatus.PAUSED,
            AgenticRunStatus.BLOCKED,
            AgenticRunStatus.CANCELLING,
            AgenticRunStatus.FAILED,
            AgenticRunStatus.COMPLETED,
            AgenticRunStatus.EXPIRED,
            AgenticRunStatus.PROVIDER_UNAVAILABLE,
        }
    ),
    AgenticRunStatus.REVIEWING: frozenset(
        {
            AgenticRunStatus.RUNNING,
            AgenticRunStatus.AWAITING_APPROVAL,
            AgenticRunStatus.PAUSED,
            AgenticRunStatus.BLOCKED,
            AgenticRunStatus.CANCELLING,
            AgenticRunStatus.FAILED,
            AgenticRunStatus.COMPLETED,
            AgenticRunStatus.EXPIRED,
            AgenticRunStatus.PROVIDER_UNAVAILABLE,
        }
    ),
    AgenticRunStatus.PAUSED: frozenset(
        {
            AgenticRunStatus.QUEUED,
            AgenticRunStatus.PLANNING,
            AgenticRunStatus.AWAITING_APPROVAL,
            AgenticRunStatus.RUNNING,
            AgenticRunStatus.VERIFYING,
            AgenticRunStatus.REVIEWING,
            AgenticRunStatus.BLOCKED,
            AgenticRunStatus.CANCELLING,
            AgenticRunStatus.FAILED,
            AgenticRunStatus.EXPIRED,
            AgenticRunStatus.PROVIDER_UNAVAILABLE,
        }
    ),
    AgenticRunStatus.BLOCKED: frozenset(
        {
            AgenticRunStatus.QUEUED,
            AgenticRunStatus.PROVISIONING,
            AgenticRunStatus.PLANNING,
            AgenticRunStatus.AWAITING_APPROVAL,
            AgenticRunStatus.RUNNING,
            AgenticRunStatus.VERIFYING,
            AgenticRunStatus.REVIEWING,
            AgenticRunStatus.CANCELLING,
            AgenticRunStatus.CANCELLED,
            AgenticRunStatus.FAILED,
            AgenticRunStatus.EXPIRED,
            AgenticRunStatus.PROVIDER_UNAVAILABLE,
        }
    ),
    AgenticRunStatus.CANCELLING: frozenset(
        {AgenticRunStatus.CANCELLED, AgenticRunStatus.FAILED}
    ),
    AgenticRunStatus.CANCELLED: frozenset(),
    AgenticRunStatus.FAILED: frozenset(),
    AgenticRunStatus.COMPLETED: frozenset(),
    AgenticRunStatus.EXPIRED: frozenset(),
    AgenticRunStatus.PROVIDER_UNAVAILABLE: frozenset(),
}


class InvalidRunTransition(ValueError):
    """Transition interdite par la machine d'état canonique."""


def validate_run_transition(
    current: AgenticRunStatus | str,
    target: AgenticRunStatus | str,
) -> tuple[AgenticRunStatus, AgenticRunStatus]:
    current_status = AgenticRunStatus(current)
    target_status = AgenticRunStatus(target)
    if current_status == target_status:
        return current_status, target_status
    if target_status not in ALLOWED_RUN_TRANSITIONS[current_status]:
        raise InvalidRunTransition(
            f"transition interdite: {current_status.value} -> {target_status.value}"
        )
    return current_status, target_status


class AgenticRequestCategory(str, Enum):
    DIRECT_ACTION = "direct_action"
    WORKFLOW = "workflow"
    AGENTIC_READONLY = "agentic_readonly"
    AGENTIC_REVERSIBLE = "agentic_reversible"
    AGENTIC_EXTERNAL_EFFECT = "agentic_external_effect"
    AGENTIC_HIGH_RISK = "agentic_high_risk"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class VerificationVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"


class RuntimeHealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class AgenticErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    INVALID_TRANSITION = "invalid_transition"
    RUNTIME_NOT_FOUND = "runtime_not_found"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    RUNTIME_PROTOCOL = "runtime_protocol"
    PERMISSION_DENIED = "permission_denied"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_ALREADY_DECIDED = "approval_already_decided"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    VERIFICATION_FAILED = "verification_failed"
    PERSISTENCE_CONFLICT = "persistence_conflict"
    INTERNAL = "internal"


@dataclass(frozen=True)
class AgenticError:
    code: AgenticErrorCode
    message: str
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", _frozen_mapping(self.details))


@dataclass(frozen=True)
class RunBudget:
    max_duration_s: float = 1800.0
    max_steps: int = 50
    max_tool_calls: int = 100
    max_retries: int = 3
    model_token_budget: int = 200_000
    cost_budget: float | None = None
    concurrency_limit: int = 1
    deadline: datetime | None = None
    max_artifact_bytes: int = 50 * 1024 * 1024
    max_context_tokens: int = 128_000
    compaction_policy: str = "checkpoint"
    blocking_strategy: str = "pause"

    def __post_init__(self) -> None:
        positive = {
            "max_duration_s": self.max_duration_s,
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "max_retries": self.max_retries,
            "model_token_budget": self.model_token_budget,
            "concurrency_limit": self.concurrency_limit,
            "max_artifact_bytes": self.max_artifact_bytes,
            "max_context_tokens": self.max_context_tokens,
        }
        for name, value in positive.items():
            minimum = (
                0
                if name in {"max_retries", "max_tool_calls", "max_artifact_bytes"}
                else 1
            )
            if value < minimum:
                raise ValueError(f"{name} doit être >= {minimum}")
        if self.cost_budget is not None and self.cost_budget < 0:
            raise ValueError("cost_budget doit être positif")


@dataclass(frozen=True)
class ToolCapability:
    name: str
    scope: str
    description: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    reversible: bool = True
    timeout_s: float = 60.0

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.scope.strip():
            raise ValueError("name et scope de capacité requis")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s doit être positif")


@dataclass(frozen=True)
class AgenticContext:
    run_id: str
    profile_id: str
    conversation_id: str | None = None
    task_id: str | None = None
    channel: str = "api"
    device: str | None = None
    locale: str = "fr-FR"
    timezone: str = "Europe/Paris"
    permissions: tuple[str, ...] = ()
    selected_context: Mapping[str, Any] = field(default_factory=dict)
    origin: str = "user"
    bypass_agentic_reclassification: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "permissions", tuple(dict.fromkeys(self.permissions)))
        object.__setattr__(
            self, "selected_context", _frozen_mapping(self.selected_context)
        )


@dataclass(frozen=True)
class AgenticClassification:
    category: AgenticRequestCategory
    reason: str
    bypassed: bool = False


@dataclass(frozen=True)
class AgenticRun:
    run_id: str
    profile_id: str
    origin: str
    channel: str
    runtime_id: str
    title: str
    status: AgenticRunStatus = AgenticRunStatus.CREATED
    phase: str = "created"
    category: AgenticRequestCategory = AgenticRequestCategory.DIRECT_ACTION
    task_id: str | None = None
    conversation_id: str | None = None
    device: str | None = None
    locale: str = "fr-FR"
    timezone: str = "Europe/Paris"
    permissions: tuple[str, ...] = ()
    selected_context: Mapping[str, Any] = field(default_factory=dict)
    provider_session_id: str | None = None
    budget: RunBudget = field(default_factory=RunBudget)
    workspace: str | None = None
    idempotency_key: str | None = None
    idempotency_digest: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = field(default_factory=utc_now)
    error: AgenticError | None = None
    verification: VerificationResult | None = None

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id requis")
        if not self.profile_id.strip():
            raise ValueError("profile_id requis")
        if not self.runtime_id.strip():
            raise ValueError("runtime_id requis")
        if (
            not self.origin.strip()
            or not self.channel.strip()
            or not self.title.strip()
        ):
            raise ValueError("origin, channel et title requis")
        if self.status is AgenticRunStatus.COMPLETED and (
            self.verification is None
            or self.verification.verdict is not VerificationVerdict.PASS
        ):
            raise ValueError("completed exige un verdict de vérification PASS")
        if self.idempotency_digest is not None and (
            len(self.idempotency_digest) != 64
            or any(char not in "0123456789abcdef" for char in self.idempotency_digest)
        ):
            raise ValueError("idempotency_digest invalide")
        device, locale, timezone_name = normalize_agentic_client_context(
            device=self.device,
            locale=self.locale,
            timezone_name=self.timezone,
        )
        object.__setattr__(self, "device", device)
        object.__setattr__(self, "locale", locale)
        object.__setattr__(self, "timezone", timezone_name)
        object.__setattr__(self, "permissions", tuple(dict.fromkeys(self.permissions)))
        object.__setattr__(
            self, "selected_context", _frozen_mapping(self.selected_context)
        )

    @property
    def id(self) -> str:
        return self.run_id

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES

    def transition(
        self,
        status: AgenticRunStatus | str,
        *,
        phase: str | None = None,
        error: AgenticError | None = None,
        verification: VerificationResult | None = None,
        now: datetime | None = None,
    ) -> AgenticRun:
        _, target = validate_run_transition(self.status, status)
        accepted_verification = verification or self.verification
        if target is AgenticRunStatus.COMPLETED and (
            accepted_verification is None
            or accepted_verification.verdict is not VerificationVerdict.PASS
        ):
            raise InvalidRunTransition(
                "un run ne peut être completed qu'après un verdict PASS"
            )
        changed_at = now or utc_now()
        started_at = self.started_at
        if started_at is None and target in {
            AgenticRunStatus.PLANNING,
            AgenticRunStatus.RUNNING,
            AgenticRunStatus.AWAITING_APPROVAL,
        }:
            started_at = changed_at
        finished_at = changed_at if target in TERMINAL_RUN_STATUSES else None
        return replace(
            self,
            status=target,
            phase=phase or target.value,
            started_at=started_at,
            finished_at=finished_at,
            updated_at=changed_at,
            error=error,
            verification=accepted_verification,
        )

    @classmethod
    def new(
        cls,
        *,
        profile_id: str,
        origin: str,
        channel: str,
        runtime_id: str,
        title: str,
        **kwargs: Any,
    ) -> AgenticRun:
        return cls(
            run_id=str(uuid.uuid4()),
            profile_id=profile_id,
            origin=origin,
            channel=channel,
            runtime_id=runtime_id,
            title=title,
            **kwargs,
        )


def canonical_run_request_digest(run: AgenticRun) -> str:
    """Lie une clé d'idempotence à la sémantique canonique de création du run."""

    budget = run.budget
    payload = {
        "profile_id": run.profile_id,
        "origin": run.origin,
        "channel": run.channel,
        "runtime_id": run.runtime_id,
        "title": run.title,
        "category": run.category.value,
        "task_id": run.task_id,
        "conversation_id": run.conversation_id,
        "device": run.device,
        "locale": run.locale,
        "timezone": run.timezone,
        "permissions": sorted(set(run.permissions)),
        "selected_context": _canonical_json_value(run.selected_context),
        "workspace": run.workspace,
        "budget": {
            "max_duration_s": budget.max_duration_s,
            "max_steps": budget.max_steps,
            "max_tool_calls": budget.max_tool_calls,
            "max_retries": budget.max_retries,
            "model_token_budget": budget.model_token_budget,
            "cost_budget": budget.cost_budget,
            "concurrency_limit": budget.concurrency_limit,
            "max_artifact_bytes": budget.max_artifact_bytes,
            "max_context_tokens": budget.max_context_tokens,
            "compaction_policy": budget.compaction_policy,
            "blocking_strategy": budget.blocking_strategy,
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    run_id: str
    sequence: int
    type: str
    timestamp: datetime = field(default_factory=utc_now)
    payload: Mapping[str, Any] = field(default_factory=dict)
    level: str = "info"
    visibility: str = "user"
    sensitivity: str = "normal"
    external_event_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.event_id.strip()
            or not self.run_id.strip()
            or not self.type.strip()
        ):
            raise ValueError("event_id, run_id et type requis")
        if self.sequence < 0:
            raise ValueError("sequence doit être positive ou nulle")
        object.__setattr__(self, "payload", _frozen_mapping(self.payload))

    @classmethod
    def new(
        cls,
        *,
        run_id: str,
        type: str,
        sequence: int = 0,
        **kwargs: Any,
    ) -> RuntimeEvent:
        return cls(
            event_id=str(uuid.uuid4()),
            run_id=run_id,
            sequence=sequence,
            type=type,
            **kwargs,
        )


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    run_id: str
    action: str
    tool: str
    summary: str
    sanitized_arguments: Mapping[str, Any] = field(default_factory=dict)
    risks: tuple[str, ...] = ()
    scope: str = "run"
    expires_at: datetime | None = None
    decision: ApprovalDecision = ApprovalDecision.PENDING
    decision_by: str | None = None
    decision_at: datetime | None = None
    decision_id: str | None = None

    def __post_init__(self) -> None:
        if not self.approval_id.strip() or not self.run_id.strip():
            raise ValueError("approval_id et run_id requis")
        if not self.action.strip() or not self.tool.strip() or not self.summary.strip():
            raise ValueError("action, tool et summary requis")
        object.__setattr__(
            self, "sanitized_arguments", _frozen_mapping(self.sanitized_arguments)
        )
        object.__setattr__(self, "risks", tuple(self.risks))


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    run_id: str
    type: str
    reference: str
    sha256: str | None = None
    size_bytes: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    visibility: str = "user"
    retention: str = "default"

    def __post_init__(self) -> None:
        if (
            not self.artifact_id.strip()
            or not self.run_id.strip()
            or not self.type.strip()
            or not self.reference.strip()
        ):
            raise ValueError("artifact_id, run_id, type et reference requis")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("size_bytes doit être positif")
        if self.sha256 is not None and (
            len(self.sha256) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in self.sha256)
        ):
            raise ValueError("sha256 invalide")
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))


@dataclass(frozen=True)
class VerificationEvidence:
    check: str
    passed: bool
    summary: str
    artifact_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.check.strip() or not self.summary.strip():
            raise ValueError("check et summary de preuve requis")
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))


@dataclass(frozen=True)
class VerificationResult:
    verdict: VerificationVerdict
    verifier: str
    summary: str
    evidence: tuple[VerificationEvidence, ...] = ()
    verified_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.verifier.strip() or not self.summary.strip():
            raise ValueError("verifier et summary requis")
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if self.verdict is VerificationVerdict.PASS and (
            not self.evidence or not all(item.passed for item in self.evidence)
        ):
            raise ValueError("un verdict PASS exige des preuves toutes validées")


@dataclass(frozen=True)
class Verifier:
    name: str
    task_types: tuple[str, ...]
    checks: tuple[str, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.task_types or not self.checks:
            raise ValueError("name, task_types et checks du verifier requis")
        object.__setattr__(self, "task_types", tuple(self.task_types))
        object.__setattr__(self, "checks", tuple(self.checks))


@dataclass(frozen=True)
class RuntimeHealth:
    status: RuntimeHealthStatus
    version: str | None = None
    message: str | None = None
    checked_at: datetime = field(default_factory=utc_now)
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", _frozen_mapping(self.details))


@dataclass(frozen=True)
class RuntimePluginManifest:
    runtime_id: str
    name: str
    version: str
    entrypoint: str
    root: Path
    capabilities: tuple[ToolCapability, ...] = ()
    enabled: bool = True
    manifest_version: int = 1
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            not self.runtime_id.strip()
            or not self.name.strip()
            or not self.version.strip()
            or not self.entrypoint.strip()
        ):
            raise ValueError("manifest runtime incomplet")
        object.__setattr__(self, "root", self.root.resolve())
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))


__all__ = [
    "ALLOWED_RUN_TRANSITIONS",
    "TERMINAL_RUN_STATUSES",
    "AgenticClassification",
    "AgenticContext",
    "AgenticError",
    "AgenticErrorCode",
    "AgenticRequestCategory",
    "AgenticRun",
    "AgenticRunStatus",
    "ApprovalDecision",
    "ApprovalRequest",
    "Artifact",
    "InvalidRunTransition",
    "RiskLevel",
    "RunBudget",
    "RuntimeEvent",
    "RuntimeHealth",
    "RuntimeHealthStatus",
    "RuntimePluginManifest",
    "ToolCapability",
    "VerificationEvidence",
    "VerificationResult",
    "VerificationVerdict",
    "Verifier",
    "canonical_run_request_digest",
    "normalize_agentic_client_context",
    "utc_now",
    "validate_run_transition",
]

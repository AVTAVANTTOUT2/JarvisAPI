"""API publique, générique et isolée par profil pour les runs agentiques."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Annotated, Any, Literal, Mapping

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

import config
from api.errors import api_error
from database import current_profile_id
from database.agentic import (
    AgenticIdempotencyConflict,
    AgenticPersistenceConflict,
    AgenticRunNotFound,
    ApprovalAlreadyDecided,
    ApprovalExpired,
)
from jarvis.agentic import (
    get_agentic_service,
    select_capability_profile,
)
from jarvis.agentic.classifier import classify_agentic_request
from jarvis.agentic.models import (
    AgenticRunStatus,
    ApprovalDecision,
    InvalidRunTransition,
    RunBudget,
)
from jarvis.agentic.registry import RuntimePluginError


router = APIRouter(prefix="/api/agentic", tags=["agentic"])

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{7,191}$")
_PUBLIC_PERMISSION_ALLOWLIST = frozenset({"tasks:read", "workspace:read"})
_RESERVED_CONTEXT_KEYS = frozenset(
    {
        "_jarvis",
        "bypass_agentic_reclassification",
        "category",
        "conversation_history",
        "origin",
        "permissions",
        "retrieval_context",
        "retrieval_references",
        "retrieval_status",
        "turn_snapshot_id",
    }
)


def _request_idempotency_digest(body: "CreateRunRequest") -> str:
    """Hash only the caller-controlled request, not time-varying enrichment."""

    payload = body.model_dump(mode="json", exclude_none=False)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RunBudgetRequest(BaseModel):
    """Limites décidées par JARVIS, jamais par le fournisseur."""

    model_config = ConfigDict(extra="forbid")

    max_duration_s: float = Field(default=1800.0, gt=0, le=86_400)
    max_steps: int = Field(default=50, ge=1, le=1_000)
    max_tool_calls: int = Field(default=100, ge=0, le=5_000)
    max_retries: int = Field(default=3, ge=0, le=20)
    model_token_budget: int = Field(default=200_000, ge=1, le=10_000_000)
    cost_budget: float | None = Field(default=None, ge=0, le=10_000)
    concurrency_limit: int = Field(default=1, ge=1, le=16)
    max_artifact_bytes: int = Field(default=50 * 1024 * 1024, ge=0, le=1024**3)
    max_context_tokens: int = Field(default=128_000, ge=1, le=10_000_000)
    compaction_policy: Literal["checkpoint", "summarize", "fail"] = "checkpoint"
    blocking_strategy: Literal["pause", "fail"] = "pause"

    def to_domain(self) -> RunBudget:
        return RunBudget(**self.model_dump())


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=4_000)
    request: str | None = Field(default=None, min_length=1, max_length=8_000)
    runtime_id: str | None = Field(default=None, max_length=64)
    category: Literal["direct_action", "workflow", "agentic_readonly"] = "direct_action"
    origin: Literal["user"] = "user"
    channel: Literal[
        "api", "web", "voice", "mobile", "imessage", "macos", "android"
    ] = "api"
    task_id: str | None = Field(default=None, max_length=128)
    conversation_id: str | None = Field(default=None, max_length=128)
    device: str | None = Field(default=None, max_length=128)
    locale: str = Field(default="fr-FR", min_length=2, max_length=32)
    timezone: str = Field(default="Europe/Paris", min_length=1, max_length=64)
    permissions: list[str] = Field(default_factory=list, max_length=100)
    selected_context: dict[str, Any] = Field(default_factory=dict)
    budget: RunBudgetRequest | None = None
    run_id: str | None = None

    @field_validator("runtime_id", "run_id", "task_id", "conversation_id")
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        if value is not None and not _ID_RE.fullmatch(value):
            raise ValueError("identifiant invalide")
        return value

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            candidate = item.strip()
            if not _ID_RE.fullmatch(candidate):
                raise ValueError("permission invalide")
            if candidate not in _PUBLIC_PERMISSION_ALLOWLIST:
                raise ValueError("permission non accordable par cette API")
            if candidate not in normalized:
                normalized.append(candidate)
        return normalized

    @field_validator("selected_context")
    @classmethod
    def validate_selected_context(cls, value: dict[str, Any]) -> dict[str, Any]:
        reserved = _RESERVED_CONTEXT_KEYS.intersection(value)
        if reserved:
            raise ValueError("clé de contexte réservée")
        return value


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "denied"]


def _jsonable(value: Any) -> Any:
    """Sérialise le domaine sans dépendre d'un DTO fournisseur."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _jsonable(getattr(value, item.name)) for item in fields(value)
        }
    raise TypeError(
        f"type de réponse agentique non sérialisable: {type(value).__name__}"
    )


def _require_run(run_id: str):
    if not _ID_RE.fullmatch(run_id):
        raise api_error(400, "invalid_run_id", "Identifiant de run invalide")
    run = get_agentic_service().get(run_id)
    if run is None:
        raise api_error(404, "agentic_run_not_found", "Run agentique introuvable")
    # Le middleware sélectionne déjà la base du profil. Ce contrôle explicite
    # empêche toute régression si le stockage devient partagé ultérieurement.
    if run.profile_id != current_profile_id():
        raise api_error(404, "agentic_run_not_found", "Run agentique introuvable")
    return run


def _decision_actor(request: Request) -> str:
    session = getattr(request.state, "session", None)
    if isinstance(session, Mapping) and session.get("id"):
        return f"session:{session['id']}"
    device = getattr(request.state, "mobile_device", None)
    if isinstance(device, Mapping) and device.get("device_id"):
        return f"device:{device['device_id']}"
    return "authenticated-user"


def _translate_domain_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AgenticRunNotFound):
        return api_error(404, "agentic_run_not_found", "Run agentique introuvable")
    if isinstance(exc, ApprovalExpired):
        return api_error(409, "approval_expired", "Cette approbation a expiré")
    if isinstance(exc, AgenticIdempotencyConflict):
        return api_error(
            409,
            "idempotency_payload_conflict",
            "Cette clé d'idempotence est liée à une autre requête",
        )
    if isinstance(exc, ApprovalAlreadyDecided):
        return api_error(
            409, "approval_already_decided", "Cette approbation a déjà été décidée"
        )
    if isinstance(exc, AgenticPersistenceConflict):
        return api_error(
            409, "agentic_persistence_conflict", "Conflit de persistance agentique"
        )
    if isinstance(exc, InvalidRunTransition):
        return api_error(409, "invalid_run_transition", "Transition de run impossible")
    if isinstance(exc, PermissionError):
        return api_error(403, "agentic_profile_forbidden", "Accès au profil interdit")
    if isinstance(exc, RuntimePluginError):
        return api_error(
            503, "agentic_runtime_unavailable", "Runtime agentique indisponible"
        )
    if isinstance(exc, ValueError):
        return api_error(400, "invalid_agentic_request", "Requête agentique invalide")
    return api_error(
        500, "agentic_internal_error", "Erreur interne du runtime agentique"
    )


@router.get("/runtime/status")
async def agentic_runtime_status() -> dict[str, Any]:
    try:
        runtimes = await get_agentic_service().runtime_status()
    except Exception as exc:  # frontière API : aucune erreur fournisseur ne fuite
        raise _translate_domain_error(exc) from exc
    return {"runtimes": _jsonable(runtimes)}


@router.post("/runs", status_code=202)
async def create_agentic_run(
    body: CreateRunRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    if idempotency_key is not None and not _IDEMPOTENCY_RE.fullmatch(idempotency_key):
        raise api_error(400, "invalid_idempotency_key", "Clé d'idempotence invalide")
    runtime_id = body.runtime_id or str(getattr(config, "AGENTIC_RUNTIME", "auto"))
    request_text = body.request or body.title
    trusted_category = classify_agentic_request(
        request_text,
        origin="user",
    ).category
    capability_profile = select_capability_profile(
        request_text,
        trusted_category,
        default_profile_id=str(
            getattr(config, "AGENTIC_DEFAULT_PROFILE", "readonly-research")
        ),
        route_overrides=getattr(config, "AGENTIC_PROFILE_ROUTE_OVERRIDES", {}),
    )
    from api.chat_context import prepare_turn

    snapshot = await prepare_turn(
        request_text,
        int(body.conversation_id)
        if body.conversation_id and body.conversation_id.isdecimal()
        else None,
        interaction_mode="voice" if body.channel == "voice" else "agentic",
    )
    permissions = (
        tuple(body.permissions)
        if body.permissions
        else capability_profile.default_permissions
    )
    try:
        run = await get_agentic_service().create_and_start(
            title=body.title,
            runtime_id=runtime_id,
            profile_id=current_profile_id(),
            origin="user",
            channel=body.channel,
            task_id=body.task_id,
            conversation_id=body.conversation_id,
            device=body.device,
            locale=body.locale,
            timezone_name=body.timezone,
            permissions=permissions,
            capability_profile_id=capability_profile.profile_id,
            selected_context={
                **body.selected_context,
                "request": request_text,
                **snapshot.agentic_context(),
            },
            category=trusted_category,
            budget=body.budget.to_domain() if body.budget else None,
            idempotency_key=idempotency_key,
            idempotency_digest=(
                _request_idempotency_digest(body) if idempotency_key else None
            ),
            run_id=body.run_id,
        )
    except Exception as exc:
        raise _translate_domain_error(exc) from exc
    return {"run": _jsonable(run), "knowledge": snapshot.public_payload()}


@router.get("/runs")
async def list_agentic_runs(
    status: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    statuses: tuple[AgenticRunStatus, ...] | None = None
    if status:
        try:
            statuses = tuple(AgenticRunStatus(item) for item in status)
        except ValueError as exc:
            raise api_error(
                400, "invalid_agentic_status", "Statut de run invalide"
            ) from exc
    runs = get_agentic_service().list(statuses=statuses, limit=limit + 1, offset=offset)
    visible = [run for run in runs if run.profile_id == current_profile_id()]
    has_more = len(visible) > limit
    visible = visible[:limit]
    return {
        "runs": _jsonable(visible),
        "pagination": {
            "limit": limit,
            "offset": offset,
            "next_offset": offset + limit if has_more else None,
        },
    }


@router.get("/runs/{run_id}")
async def get_agentic_run(run_id: str) -> dict[str, Any]:
    return {"run": _jsonable(_require_run(run_id))}


@router.get("/runs/{run_id}/events")
async def get_agentic_run_events(
    run_id: str,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    _require_run(run_id)
    events = get_agentic_service().events(run_id, after_sequence=after_sequence)
    return {"events": _jsonable(events)}


@router.get("/runs/{run_id}/artifacts")
async def get_agentic_run_artifacts(run_id: str) -> dict[str, Any]:
    _require_run(run_id)
    artifacts = get_agentic_service().artifacts(run_id)
    return {"artifacts": _jsonable(artifacts)}


@router.get("/runs/{run_id}/approvals")
async def get_agentic_run_approvals(run_id: str) -> dict[str, Any]:
    _require_run(run_id)
    approvals = get_agentic_service().approvals(run_id)
    return {"approvals": _jsonable(approvals)}


async def _run_action(run_id: str, action: str) -> dict[str, Any]:
    _require_run(run_id)
    service = get_agentic_service()
    try:
        run = await getattr(service, action)(run_id)
    except Exception as exc:
        raise _translate_domain_error(exc) from exc
    return {"run": _jsonable(run)}


@router.post("/runs/{run_id}/pause")
async def pause_agentic_run(run_id: str) -> dict[str, Any]:
    return await _run_action(run_id, "pause")


@router.post("/runs/{run_id}/resume")
async def resume_agentic_run(run_id: str) -> dict[str, Any]:
    return await _run_action(run_id, "resume")


@router.post("/runs/{run_id}/cancel")
async def cancel_agentic_run(run_id: str) -> dict[str, Any]:
    return await _run_action(run_id, "cancel")


@router.post("/runs/{run_id}/approvals/{approval_id}/decision")
async def decide_agentic_approval(
    run_id: str,
    approval_id: str,
    body: ApprovalDecisionRequest,
    request: Request,
    decision_id: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    _require_run(run_id)
    if not _ID_RE.fullmatch(approval_id):
        raise api_error(
            400, "invalid_approval_id", "Identifiant d'approbation invalide"
        )
    if not _IDEMPOTENCY_RE.fullmatch(decision_id):
        raise api_error(400, "invalid_idempotency_key", "Clé d'idempotence invalide")
    try:
        approval = await get_agentic_service().decide_approval(
            run_id,
            approval_id,
            ApprovalDecision(body.decision),
            decided_by=_decision_actor(request),
            decision_id=decision_id,
        )
    except Exception as exc:
        raise _translate_domain_error(exc) from exc
    return {"approval": _jsonable(approval)}


__all__ = ["router"]

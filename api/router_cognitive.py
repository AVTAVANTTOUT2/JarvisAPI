"""API — routage cognitif, délégations Cursor, briefings, capacités."""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["cognitive"])

_TEMPLATE_ID_RE = re.compile(r"^[a-z0-9_:-]{1,80}$")


class RouteRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)
    interaction_mode: Literal["chat", "voice", "android", "loop"] = "chat"


class RequiredTestSpec(BaseModel):
    executable: str = Field(..., min_length=1, max_length=64)
    args: list[str] = Field(default_factory=list, max_length=32)
    cwd: str | None = Field(default=None, max_length=200)
    timeout_seconds: int = Field(default=900, ge=1, le=900)


class CursorEnqueueRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    user_request: str = Field(..., min_length=1, max_length=20000)
    template_id: str = "feature_implementation"
    risk_level: Literal["low", "medium", "high"] = "medium"
    auto_start: bool = False
    require_confirmation: bool = True
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)
    required_tests: list[str | RequiredTestSpec] = Field(default_factory=list, max_length=3)

    @field_validator("template_id")
    @classmethod
    def _validate_template(cls, value: str) -> str:
        if not _TEMPLATE_ID_RE.match(value or ""):
            raise ValueError("template_id invalide")
        return value


class BriefingRequest(BaseModel):
    kind: Literal["morning", "evening", "delta"] = "morning"
    voice_only: bool = False
    filter_priority: str | None = None
    work_only: bool = False


@router.post("/cognitive/route")
async def cognitive_route(body: RouteRequest) -> dict[str, Any]:
    from jarvis.cognitive import route_request

    intent = route_request(body.text, interaction_mode=body.interaction_mode)
    return {"ok": True, "routing": intent.to_diagnostic()}


@router.get("/cognitive/capabilities")
async def cognitive_capabilities() -> dict[str, Any]:
    from jarvis.cognitive import get_capability_registry

    reg = get_capability_registry()
    reg.refresh()
    return {"ok": True, "capabilities": reg.list_all()}


@router.get("/cognitive/llm-policy")
async def llm_policy() -> dict[str, Any]:
    import config

    return {
        "ok": True,
        "policy": {
            "voice": getattr(config, "VOICE_REASONING_MODEL", config.DEEPSEEK_FAST_MODEL),
            "main": getattr(config, "MAIN_REASONING_MODEL", config.DEEPSEEK_MAIN_MODEL),
            "cursor": "Cursor CLI (agent --print)",
            "ollama": "screen_watcher only",
            "ollama_reasoning_enabled": bool(getattr(config, "OLLAMA_REASONING_ENABLED", False)),
        },
    }


@router.get("/cursor/status")
async def cursor_status() -> dict[str, Any]:
    from integrations.cursor_delegation import cursor_delegation

    cli = cursor_delegation.cli_status()
    # Ne pas exposer help_text brut ni chemins sensibles
    safe = {
        "available": cli.get("available"),
        "authenticated": cli.get("authenticated"),
        "version": cli.get("version"),
        "supports_print": cli.get("supports_print"),
        "supports_workspace": cli.get("supports_workspace"),
        "supports_force": cli.get("supports_force"),
        "supports_trust": cli.get("supports_trust"),
        "error": cli.get("error"),
    }
    return {"ok": True, "cli": safe}


@router.get("/cursor/jobs")
async def cursor_jobs(
    limit: int = Query(50, ge=1, le=200),
    status: str | None = None,
    diagnostic: bool = Query(False, description="Vue diagnostic (toujours redacted)"),
) -> dict[str, Any]:
    from integrations.cursor_delegation import cursor_delegation

    return {
        "ok": True,
        "jobs": cursor_delegation.list_jobs(
            limit=limit, status=status, public=not diagnostic
        ),
    }


@router.get("/cursor/jobs/{job_id}")
async def cursor_job_detail(
    job_id: str,
    diagnostic: bool = Query(False),
) -> dict[str, Any]:
    from integrations.cursor_delegation import cursor_delegation

    job = cursor_delegation.get_job(job_id, public=not diagnostic, diagnostic=diagnostic)
    if not job:
        raise HTTPException(404, f"Job {job_id} introuvable")
    return {"ok": True, "job": job}


@router.post("/cursor/jobs")
async def cursor_enqueue(body: CursorEnqueueRequest) -> dict[str, Any]:
    from integrations.cursor_delegation import CursorDelegationError, cursor_delegation
    from jarvis.cognitive import route_request
    from jarvis.security.redaction import public_cursor_job_view

    intent = route_request(body.user_request, interaction_mode="chat")
    # Normalise required_tests en dicts structurés
    specs: list[Any] = []
    for item in body.required_tests:
        if isinstance(item, RequiredTestSpec):
            specs.append(item.model_dump())
        else:
            specs.append(item)

    # Chat API : confirmation obligatoire sauf demande explicite ET mode autonome
    require_confirmation = body.require_confirmation
    auto_start = body.auto_start and not require_confirmation
    try:
        job = await cursor_delegation.enqueue(
            title=body.title,
            user_request=body.user_request,
            template_id=body.template_id or intent.template_id or "feature_implementation",
            risk_level=body.risk_level,
            acceptance_criteria=body.acceptance_criteria or None,
            required_tests=specs or None,
            routing=intent.to_diagnostic(),
            auto_start=auto_start,
            require_confirmation=require_confirmation,
        )
    except CursorDelegationError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "job": public_cursor_job_view(job)}


@router.post("/cursor/jobs/{job_id}/confirm")
async def cursor_confirm(job_id: str) -> dict[str, Any]:
    """Confirmation explicite — démarre le job en awaiting_confirmation."""
    from integrations.cursor_delegation import CursorDelegationError, cursor_delegation
    from jarvis.security.redaction import public_cursor_job_view

    try:
        job = await cursor_delegation.confirm(job_id)
    except CursorDelegationError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not job:
        raise HTTPException(404, f"Job {job_id} introuvable")
    return {"ok": True, "job": public_cursor_job_view(job)}


@router.post("/cursor/jobs/{job_id}/cancel")
async def cursor_cancel(job_id: str) -> dict[str, Any]:
    from integrations.cursor_delegation import cursor_delegation
    from jarvis.security.redaction import public_cursor_job_view

    job = cursor_delegation.cancel(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} introuvable")
    return {"ok": True, "job": public_cursor_job_view(job)}


@router.post("/cursor/jobs/{job_id}/rollback")
async def cursor_rollback(job_id: str) -> dict[str, Any]:
    from integrations.cursor_delegation import cursor_delegation
    from jarvis.security.redaction import public_cursor_job_view

    job = cursor_delegation.rollback(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} introuvable")
    return {"ok": True, "job": public_cursor_job_view(job)}


@router.post("/cursor/jobs/{job_id}/retry")
async def cursor_retry(job_id: str) -> dict[str, Any]:
    from integrations.cursor_delegation import cursor_delegation
    from jarvis.security.redaction import public_cursor_job_view
    from database.cursor_jobs import get_cursor_job

    # Lecture brute DB (redacted ensuite) — la vue publique n'a pas user_request
    old = get_cursor_job(job_id)
    if not old:
        raise HTTPException(404, f"Job {job_id} introuvable")
    job = await cursor_delegation.enqueue(
        title=f"Retry: {old['title']}",
        user_request=old["user_request"],
        template_id=old.get("prompt_template") or "feature_implementation",
        risk_level=old.get("risk_level") or "medium",
        auto_start=False,
        require_confirmation=True,
    )
    return {"ok": True, "job": public_cursor_job_view(job)}


@router.post("/briefings/generate")
async def briefing_generate(body: BriefingRequest) -> dict[str, Any]:
    from agents.briefing_engine import generate_structured_briefing

    briefing = await generate_structured_briefing(
        kind=body.kind,
        voice_only=body.voice_only,
        filter_priority=body.filter_priority,  # type: ignore[arg-type]
        work_only=body.work_only,
    )
    return {"ok": True, "briefing": briefing.to_dict()}


@router.get("/voice/metrics")
async def voice_metrics(days: int = Query(7, ge=1, le=90)) -> dict[str, Any]:
    """P50 / p95 par étape du pipeline vocal (STT, LLM, TTS, total)."""
    from database import get_voice_latency_metrics

    return {"ok": True, **get_voice_latency_metrics(days)}


@router.get("/contacts/resolve")
async def contacts_resolve(q: str = Query(..., min_length=1, max_length=200)) -> dict[str, Any]:
    from integrations.contact_resolver import resolve_contact_query

    return {"ok": True, **resolve_contact_query(q)}


@router.get("/improvements/proposals")
async def improvement_proposals(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    from scripts.self_improvement import list_proposals

    return {"ok": True, "proposals": list_proposals(limit)}


@router.post("/improvements/run")
async def improvement_run(auto_delegate: bool = False) -> dict[str, Any]:
    from scripts.self_improvement import propose_improvements

    return await propose_improvements(auto_delegate=auto_delegate)


@router.get("/autonomy/settings")
async def autonomy_settings() -> dict[str, Any]:
    import config

    return {
        "ok": True,
        "settings": {
            "self_repair_enabled": bool(getattr(config, "SELF_REPAIR_ENABLED", False)),
            "self_improvement_enabled": bool(getattr(config, "SELF_IMPROVEMENT_ENABLED", False)),
            "self_modification_mode": getattr(config, "SELF_MODIFICATION_MODE", "pr_only"),
            "cursor_delegation_enabled": bool(getattr(config, "CURSOR_DELEGATION_ENABLED", True)),
            "cursor_allow_pr": bool(getattr(config, "CURSOR_ALLOW_PR", False)),
            "cursor_allow_merge": bool(getattr(config, "CURSOR_ALLOW_MERGE", False)),
            "cursor_max_concurrent_jobs": int(getattr(config, "CURSOR_MAX_CONCURRENT_JOBS", 2)),
        },
    }

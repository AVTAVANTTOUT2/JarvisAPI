"""Relais visuel local, en lecture seule et pré-neutralisé.

Ce contrat n'expose ni session utilisateur, ni prompt, ni arguments d'outil.
Son jeton de service ne donne accès qu'aux trois routes déclarées ici.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

import config
from core.network_security import is_loopback_request
from database import get_event_replay_window
from jarvis.agentic import get_agentic_service


router = APIRouter(prefix="/api/visual/v1", tags=["visual"])

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_INITIAL_HISTORY = 30
_REPLAY_LIMIT = 1_000
_POLL_SECONDS = 0.5
_HEARTBEAT_SECONDS = 15.0
_VISUAL_EVENT_TYPES = frozenset(
    {
        "agent.run.created",
        "agent.run.classified",
        "agent.run.queued",
        "agent.run.provisioning",
        "agent.run.phase_changed",
        "agent.run.started",
        "agent.run.paused",
        "agent.run.resumed",
        "agent.run.blocked",
        "agent.run.verifying",
        "agent.run.reviewing",
        "agent.run.completed",
        "agent.run.failed",
        "agent.run.cancelled",
        "agent.run.expired",
        "agent.run.provider_unavailable",
        "agent.tool.started",
        "agent.tool.completed",
        "agent.tool.failed",
        "agent.approval.requested",
        "agent.approval.resolved",
    }
)
_PROGRESS_BY_STATUS = {
    "created": 0,
    "classified": 5,
    "queued": 10,
    "provisioning": 15,
    "planning": 25,
    "awaiting_approval": 35,
    "running": 50,
    "paused": 50,
    "blocked": 50,
    "cancelling": 90,
    "verifying": 80,
    "reviewing": 90,
    "cancelled": 100,
    "failed": 100,
    "completed": 100,
    "expired": 100,
    "provider_unavailable": 100,
}


def _visual_token_path() -> Path:
    configured = Path(config.VISUAL_RELAY_TOKEN_FILE)
    root = Path(config.BASE_DIR).resolve()
    try:
        resolved = configured.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(503, "visual relay unavailable") from exc
    try:
        metadata = resolved.stat()
    except OSError as exc:
        raise HTTPException(503, "visual relay unavailable") from exc
    if (
        configured.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or not 1 <= metadata.st_size <= 256
    ):
        raise HTTPException(503, "visual relay unavailable")
    return resolved


def _require_visual_read(request: Request) -> None:
    if not is_loopback_request(request):
        raise HTTPException(403, "visual relay is loopback-only")
    authorization = request.headers.get("authorization", "")
    scheme, separator, candidate = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not _TOKEN_RE.fullmatch(candidate):
        raise HTTPException(
            401,
            "visual read token required",
            headers={"WWW-Authenticate": 'Bearer scope="visual:read"'},
        )
    try:
        expected = _visual_token_path().read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise HTTPException(503, "visual relay unavailable") from exc
    if not _TOKEN_RE.fullmatch(expected) or not secrets.compare_digest(candidate, expected):
        raise HTTPException(
            401,
            "invalid visual read token",
            headers={"WWW-Authenticate": 'Bearer scope="visual:read"'},
        )


def _parse_last_event_id(raw: str | None) -> tuple[int | None, str | None]:
    if raw is None or not raw.strip():
        return None, None
    if not re.fullmatch(r"[0-9]{1,20}", raw.strip()):
        return None, "invalid-last-event-id"
    return int(raw), None


def _role_for(status: str, phase: str) -> str:
    if status == "planning" or "plan" in phase:
        return "planner"
    if status in {"verifying", "reviewing"} or "review" in phase or "verif" in phase:
        return "reviewer"
    if "code" in phase or "edit" in phase or "test" in phase:
        return "coding"
    return "executor"


def _neutral_run_view(run: Any) -> dict[str, Any]:
    status = str(getattr(getattr(run, "status", "created"), "value", getattr(run, "status", "created")))
    phase = str(getattr(run, "phase", status))[:64]
    return {
        "run_id": str(run.run_id),
        "title": "Tâche agentique JARVIS",
        "status": status,
        "phase": phase,
        "channel": str(getattr(run, "channel", "agentic"))[:64],
        "role": _role_for(status, phase),
        "progress": _PROGRESS_BY_STATUS.get(status, 0),
        "needs_attention": status in {"awaiting_approval", "blocked", "failed", "provider_unavailable"},
    }


def _neutral_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    event_type = str(event.get("event_type") or event.get("type") or "")
    if event_type not in _VISUAL_EVENT_TYPES:
        return None
    payload = event.get("payload")
    data = payload if isinstance(payload, Mapping) else {}
    run_id = str(data.get("run_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", run_id):
        return None
    status = str(data.get("status") or "running")[:32]
    phase = str(data.get("phase") or status)[:64]
    return {
        "event_id": str(event.get("event_id") or f"visual-{event['sse_id']}"),
        "event_type": event_type,
        "type": event_type,
        "timestamp": float(event.get("timestamp") or 0),
        "source": "jarvis.agentic",
        "payload": {
            "run_id": run_id,
            "title": "Tâche agentique JARVIS",
            "status": status,
            "phase": phase,
            "channel": str(data.get("channel") or "agentic")[:64],
            "role": _role_for(status, phase),
            "progress": _PROGRESS_BY_STATUS.get(status, 0),
            "step": event_type,
            "needs_attention": bool(data.get("needs_attention"))
            or status in {"awaiting_approval", "blocked", "failed", "provider_unavailable"},
        },
    }


def _format_event(sse_id: int, event: Mapping[str, Any]) -> str:
    return (
        f"id: {sse_id}\n"
        f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _format_reset(reason: str, resume_after: int, skipped: int) -> str:
    payload = {
        "event_id": f"stream-reset-{resume_after}",
        "event_type": "stream.reset",
        "type": "stream.reset",
        "timestamp": time.time(),
        "source": "jarvis.visual",
        "payload": {"reason": reason[:48], "skipped": max(0, skipped)},
    }
    return (
        "event: stream.reset\n"
        f"id: {resume_after}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


async def _visual_event_stream(
    last_event_id: int | None,
    invalid_reason: str | None,
) -> AsyncIterator[str]:
    cursor = last_event_id
    initial = True
    pending_reset = invalid_reason
    last_heartbeat = time.monotonic()
    while True:
        window = await asyncio.to_thread(
            get_event_replay_window,
            cursor if not initial or cursor is not None else None,
            initial_limit=_INITIAL_HISTORY,
            replay_limit=_REPLAY_LIMIT,
        )
        reason = pending_reset or window.reset_reason
        if reason:
            yield _format_reset(reason, window.resume_after, window.skipped)
            cursor = window.resume_after
            pending_reset = None
        for event in window.events:
            event_id = int(event["sse_id"])
            if cursor is not None and event_id <= cursor:
                continue
            cursor = event_id
            visual = _neutral_event(event)
            if visual is not None:
                yield _format_event(event_id, visual)
        initial = False
        now = time.monotonic()
        if now - last_heartbeat >= _HEARTBEAT_SECONDS:
            yield ": keepalive\n\n"
            last_heartbeat = now
        await asyncio.sleep(_POLL_SECONDS)


@router.get("/health")
async def visual_health(request: Request) -> JSONResponse:
    _require_visual_read(request)
    return JSONResponse(
        {"ok": True, "scope": "visual:read", "read_only": True},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/snapshot")
async def visual_snapshot(request: Request) -> JSONResponse:
    _require_visual_read(request)
    runs = get_agentic_service().list(limit=100, offset=0)
    payload = {
        "agents_registered": ["planner", "executor", "reviewer", "coding"],
        "agentic": {"runs": [_neutral_run_view(run) for run in runs]},
    }
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@router.get("/events")
async def visual_events(request: Request) -> StreamingResponse:
    _require_visual_read(request)
    last_event_id, invalid_reason = _parse_last_event_id(request.headers.get("last-event-id"))
    return StreamingResponse(
        _visual_event_stream(last_event_id, invalid_reason),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]

"""Traduction stricte des événements OpenCode vers le contrat JARVIS."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from jarvis.agentic.models import RuntimeEvent

from .client.models import SSEEvent


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._:-]+")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_name(value: Any, fallback: str, *, limit: int = 120) -> str:
    candidate = _SAFE_NAME.sub("-", str(value or "")).strip("-._:")[:limit]
    return candidate or fallback


def _event_parts(event: SSEEvent) -> tuple[str, Mapping[str, Any]]:
    envelope = _mapping(event.data)
    event_type = str(envelope.get("type") or event.event_type or "")
    properties = _mapping(envelope.get("properties"))
    if not properties and event_type == event.event_type:
        properties = envelope
    return event_type, properties


def _belongs_to_session(properties: Mapping[str, Any], session_id: str) -> bool:
    observed = properties.get("sessionID") or properties.get("sessionId")
    if observed is None:
        nested = _mapping(properties.get("info"))
        observed = nested.get("sessionID") or nested.get("sessionId")
    return observed is not None and str(observed) == session_id


def map_opencode_event(
    *,
    run_id: str,
    session_id: str,
    event: SSEEvent,
) -> RuntimeEvent | None:
    """Mappe uniquement les signaux utiles, sans prompt, CoT ni résultat brut."""

    event_type, properties = _event_parts(event)
    if not _belongs_to_session(properties, session_id):
        return None
    payload: dict[str, Any] = {"run_id": run_id}
    target_type: str | None = None

    if event_type == "session.status":
        status_value = properties.get("status")
        status = (
            _mapping(status_value).get("type")
            if isinstance(status_value, Mapping)
            else status_value
        )
        phase = _safe_name(status, "running", limit=64)
        target_type = "agent.run.phase_changed"
        payload.update(
            {
                "phase": phase,
                "progress": 0.5,
                "needs_attention": phase == "retry",
            }
        )
    elif event_type == "session.idle":
        target_type = "agent.run.completed"
        payload.update(
            {
                "phase": "runtime_completed",
                "progress": 1.0,
                "spoken_summary": "Exécution terminée, vérification JARVIS en cours.",
            }
        )
    elif event_type == "session.error":
        target_type = "agent.run.failed"
        payload.update(
            {
                "phase": "runtime_failed",
                "needs_attention": True,
                "error_code": "runtime_error",
            }
        )
    elif event_type == "permission.asked":
        approval_id = _safe_name(properties.get("id"), "permission", limit=128)
        permission = _safe_name(properties.get("permission"), "runtime.permission")
        tool = _safe_name(properties.get("tool"), permission)
        target_type = "agent.approval.requested"
        payload.update(
            {
                "approval_id": approval_id,
                "action": permission,
                "tool": tool,
                "spoken_summary": "Une autorisation est nécessaire pour poursuivre.",
                "needs_attention": True,
            }
        )
    elif event_type == "permission.replied":
        target_type = "agent.approval.resolved"
        payload.update(
            {
                "approval_id": _safe_name(
                    properties.get("requestID") or properties.get("id"), "permission"
                ),
                "needs_attention": False,
            }
        )
    elif event_type == "message.part.updated":
        part = _mapping(properties.get("part"))
        if str(part.get("type") or "") != "tool":
            return None
        state = _mapping(part.get("state"))
        status = _safe_name(state.get("status"), "running", limit=32)
        target_type = {
            "pending": "agent.tool.started",
            "running": "agent.tool.started",
            "completed": "agent.tool.completed",
            "error": "agent.tool.failed",
            "failed": "agent.tool.failed",
        }.get(status)
        if target_type is None:
            return None
        payload.update(
            {
                "tool": _safe_name(part.get("tool"), "runtime.tool"),
                "tool_call_id": _safe_name(
                    part.get("callID") or part.get("id"), "tool-call"
                ),
                "phase": "tool",
                "needs_attention": target_type == "agent.tool.failed",
            }
        )
    elif event_type == "todo.updated":
        todos = properties.get("todos")
        if not isinstance(todos, list):
            return None
        completed = sum(
            1
            for item in todos
            if isinstance(item, Mapping)
            and str(item.get("status") or "") == "completed"
        )
        total = min(len(todos), 1_000)
        target_type = "agent.run.phase_changed"
        payload.update(
            {
                "phase": "executing_plan",
                "completed_steps": completed,
                "total_steps": total,
                "progress": completed / total if total else 0.0,
            }
        )

    if target_type is None:
        return None
    return RuntimeEvent.new(
        run_id=run_id,
        type=target_type,
        payload=payload,
        external_event_id=event.event_id,
    )


__all__ = ["map_opencode_event"]

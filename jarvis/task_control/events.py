"""Pont entre le pilotage de tâches et le bus applicatif JARVIS.

Le contexte, la voix, les notifications macOS et l'interface web consomment
tous ce même flux. Rien n'appelle directement le moteur vocal : un événement
part sur le bus, la politique de notification décide, et le moteur vocal parle
au plus une fois par événement.

La charge utile est **fermée** : une allowlist de champs, tous scalaires ou
listes courtes, tous redactés. C'est ce qui permet d'afficher un événement à
l'écran verrouillé ou de le faire lire à voix haute sans relire le contenu de
la tâche à chaque fois.
"""

from __future__ import annotations

from typing import Any, Mapping

from jarvis.agentic.redaction import redact_text
from jarvis.event_bus import EventBus, JarvisEvent, VALID_EVENT_TYPES, event_bus

TASK_CONTROL_EVENT_PREFIX = "task.control."

TASK_CONTROL_EVENT_TYPES: tuple[str, ...] = (
    "task.control.created",
    "task.control.plan_ready",
    "task.control.plan_decided",
    "task.control.started",
    "task.control.progress",
    "task.control.question",
    "task.control.permission_required",
    "task.control.blocked",
    "task.control.completed",
    "task.control.failed",
    "task.control.cancelled",
    "task.control.candidate_detected",
)

#: Champs autorisés à franchir la frontière du bus.
SAFE_TASK_EVENT_FIELDS: frozenset[str] = frozenset(
    {
        "task_id",
        "candidate_id",
        "run_id",
        "plan_version",
        "status",
        "previous_status",
        "phase",
        "title",
        "priority",
        "progress",
        "source_type",
        "source_channel",
        "needs_attention",
        "result_status",
        "spoken_summary",
        "approval_id",
        "approval_action",
        "decision",
        "confidence",
        "report_id",
        "deliverable_count",
    }
)


def neutralize_task_event_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = payload or {}
    safe: dict[str, Any] = {}
    for key in SAFE_TASK_EVENT_FIELDS:
        if key not in source:
            continue
        value = source[key]
        if key in {"progress", "confidence"}:
            try:
                safe[key] = max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                continue
        elif key in {"plan_version", "deliverable_count"}:
            try:
                safe[key] = max(0, int(value))
            except (TypeError, ValueError):
                continue
        elif key == "needs_attention":
            safe[key] = bool(value)
        elif key in {"title", "spoken_summary", "approval_action"}:
            safe[key] = redact_text(value, max_chars=240)
        else:
            safe[key] = redact_text(value, max_chars=120)
    return safe


def build_task_control_event(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    source: str = "jarvis.task_control",
) -> JarvisEvent:
    if event_type not in TASK_CONTROL_EVENT_TYPES:
        raise ValueError(f"type de pilotage inconnu: {event_type}")
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(f"type absent du bus applicatif: {event_type}")
    identifier = str(payload.get("task_id") or payload.get("candidate_id") or "").strip()
    if not identifier:
        raise ValueError("task_id ou candidate_id requis")
    complete = {
        "task_id": "",
        "status": "unknown",
        "phase": "",
        "title": "",
        "progress": 0.0,
        "needs_attention": False,
        "spoken_summary": "",
        **payload,
    }
    return JarvisEvent(
        type=event_type,
        agent="task_control",
        source=source,
        data=neutralize_task_event_payload(complete),
    )


async def emit_task_control_event(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    bus: EventBus = event_bus,
    source: str = "jarvis.task_control",
) -> JarvisEvent:
    event = build_task_control_event(event_type, payload, source=source)
    await bus.emit(event)
    return event


__all__ = [
    "SAFE_TASK_EVENT_FIELDS",
    "TASK_CONTROL_EVENT_PREFIX",
    "TASK_CONTROL_EVENT_TYPES",
    "build_task_control_event",
    "emit_task_control_event",
    "neutralize_task_event_payload",
]

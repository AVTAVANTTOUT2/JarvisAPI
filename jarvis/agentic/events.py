"""Pont sûr entre les événements agentiques et le bus JARVIS."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jarvis.event_bus import AGENTIC_EVENT_TYPES, EventBus, JarvisEvent, event_bus

from .redaction import neutralize_event_payload


def build_agentic_bus_event(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    source: str = "jarvis.agentic",
) -> JarvisEvent:
    if event_type not in AGENTIC_EVENT_TYPES:
        raise ValueError(f"type agentique inconnu: {event_type}")
    if not str(payload.get("run_id") or "").strip():
        raise ValueError("run_id requis pour un événement agentique")
    complete_payload = {
        "run_id": "",
        "status": "unknown",
        "phase": "unknown",
        "channel": "unknown",
        "title": "",
        "progress": 0.0,
        "needs_attention": False,
        "spoken_summary": "",
        **payload,
    }
    return JarvisEvent(
        type=event_type,
        agent="agentic",
        source=source,
        data=neutralize_event_payload(complete_payload),
    )


async def emit_agentic_event(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    bus: EventBus = event_bus,
    source: str = "jarvis.agentic",
) -> JarvisEvent:
    event = build_agentic_bus_event(event_type, payload, source=source)
    await bus.emit(event)
    return event


__all__ = ["build_agentic_bus_event", "emit_agentic_event"]

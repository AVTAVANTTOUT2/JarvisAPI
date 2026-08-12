"""Registre léger des connexions WebSocket actives."""

from __future__ import annotations

import asyncio
from typing import Any

from jarvis.event_bus import (
    AGENTIC_EVENT_TYPES,
    DOMAIN_EVENT_TYPES,
    JarvisEvent,
    event_bus,
)


connected_ws: set[Any] = set()
connected_ws_profiles: dict[Any, str] = {}
connected_ws_lock = asyncio.Lock()


async def add_websocket(ws: Any) -> None:
    from database.core import current_profile_id

    async with connected_ws_lock:
        connected_ws.add(ws)
        connected_ws_profiles[ws] = current_profile_id()


async def remove_websocket(ws: Any) -> None:
    async with connected_ws_lock:
        connected_ws.discard(ws)
        connected_ws_profiles.pop(ws, None)


async def broadcast_ws(event: dict[str, Any], *, profile_id: str | None = None) -> None:
    """Diffuse un événement sur un snapshot stable des connexions actives."""
    # Les I/O réseau restent hors du verrou : une socket lente ne bloque pas
    # les connexions et déconnexions concurrentes.
    from database.core import current_profile_id

    selected_profile = profile_id or current_profile_id()
    async with connected_ws_lock:
        recipients = tuple(
            ws
            for ws in connected_ws
            if connected_ws_profiles.get(ws, "default") == selected_profile
        )

    dead: set[Any] = set()
    for ws in recipients:
        try:
            await ws.send_json(event)
        except Exception:
            dead.add(ws)

    if dead:
        async with connected_ws_lock:
            connected_ws.difference_update(dead)
            for ws in dead:
                connected_ws_profiles.pop(ws, None)


@event_bus.on(DOMAIN_EVENT_TYPES)
async def broadcast_domain_event(event: JarvisEvent) -> None:
    """Pousse les mutations de domaine aux clients WebSocket connectés."""
    await broadcast_ws(event.to_dict(), profile_id=event.profile_id)


@event_bus.on(AGENTIC_EVENT_TYPES)
async def broadcast_agentic_event(event: JarvisEvent) -> None:
    """Diffuse le contrat agentique déjà neutralisé au profil propriétaire."""
    await broadcast_ws(event.to_dict(), profile_id=event.profile_id)

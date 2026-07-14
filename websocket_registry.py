"""Registre léger des connexions WebSocket actives."""

from __future__ import annotations

import asyncio
from typing import Any

from jarvis.event_bus import DOMAIN_EVENT_TYPES, JarvisEvent, event_bus


connected_ws: set[Any] = set()
connected_ws_lock = asyncio.Lock()


async def add_websocket(ws: Any) -> None:
    async with connected_ws_lock:
        connected_ws.add(ws)


async def remove_websocket(ws: Any) -> None:
    async with connected_ws_lock:
        connected_ws.discard(ws)


async def broadcast_ws(event: dict[str, Any]) -> None:
    """Diffuse un événement sur un snapshot stable des connexions actives."""
    # Les I/O réseau restent hors du verrou : une socket lente ne bloque pas
    # les connexions et déconnexions concurrentes.
    async with connected_ws_lock:
        recipients = tuple(connected_ws)

    dead: set[Any] = set()
    for ws in recipients:
        try:
            await ws.send_json(event)
        except Exception:
            dead.add(ws)

    if dead:
        async with connected_ws_lock:
            connected_ws.difference_update(dead)


@event_bus.on(DOMAIN_EVENT_TYPES)
async def broadcast_domain_event(event: JarvisEvent) -> None:
    """Pousse les mutations de domaine aux clients WebSocket connectés."""
    await broadcast_ws(event.to_dict())

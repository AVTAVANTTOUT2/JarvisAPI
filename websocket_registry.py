"""Registre léger des connexions WebSocket actives.

Toutes les mutations et lectures du registre passent par les fonctions
exportées.  Le verrou ``connected_ws_lock`` n'est jamais maintenu pendant
une opération réseau (``send_json``) — seules les mutations et les
snapshots l'utilisent.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

connected_ws: set[Any] = set()
connected_ws_lock = asyncio.Lock()


async def add_websocket(ws: Any) -> None:
    async with connected_ws_lock:
        connected_ws.add(ws)


async def remove_websocket(ws: Any) -> None:
    async with connected_ws_lock:
        connected_ws.discard(ws)


def ws_is_connected(ws: Any) -> bool:
    """Vérifie la présence d'une socket dans le registre (lecture seule, sync).

    En asyncio mono-thread, un test ``in`` sur un set est atomique du point
    de vue de la boucle événementielle (pas d'``await``, pas de context
    switch).  Pas besoin du verrou asyncio pour une lecture scalaire.
    """
    return ws in connected_ws


def ws_count() -> int:
    """Nombre de connexions actives (lecture seule, sync)."""
    return len(connected_ws)


async def broadcast_ws(event: dict[str, Any]) -> None:
    """Diffuse un événement sur un snapshot stable des connexions actives.

    Les I/O réseau restent hors du verrou : une socket lente ne bloque pas
    les connexions et déconnexions concurrentes.
    """
    async with connected_ws_lock:
        recipients = tuple(connected_ws)

    dead: list[Any] = []
    for ws in recipients:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)

    if dead:
        async with connected_ws_lock:
            for ws in dead:
                connected_ws.discard(ws)
        logger.debug("[ws-registry] %d socket(s) morte(s) retirée(s) du registre", len(dead))

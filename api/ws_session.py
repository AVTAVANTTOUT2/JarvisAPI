"""État de session WebSocket partagé et reprise de conversation."""

from __future__ import annotations

import logging
from typing import Any

import config
from database import create_conversation
from websocket_registry import connected_ws

logger = logging.getLogger("jarvis")


_ws_last_session: dict[str, Any] = {"conversation_id": None, "closed_at": 0.0, "ws": None}


def _resume_or_create_conversation(now: float | None = None) -> tuple[int, bool]:
    """Reprend la conversation précédente si la coupure est plus courte que
    VOICE_SESSION_GRACE_S, sinon en crée une nouvelle. Retourne (id, reprise).

    Deux cas de reprise :
    - déconnexion détectée il y a moins de `grace` secondes ;
    - l'ancienne socket a déjà quitté `connected_ws` sans que sa clôture soit
      horodatée (coupure brutale, handler encore en cours) — même conversation.
    """
    import time as _time

    now = now or _time.time()
    grace = getattr(config, "VOICE_SESSION_GRACE_S", 180)
    prev_id = _ws_last_session.get("conversation_id")
    if prev_id:
        closed_at = _ws_last_session.get("closed_at") or 0.0
        prev_ws = _ws_last_session.get("ws")
        recently_closed = closed_at > 0.0 and (now - closed_at) < grace
        dropped = closed_at == 0.0 and prev_ws is not None and prev_ws not in connected_ws
        if recently_closed or dropped:
            logger.info("[ws] Reprise de la conversation #%s (coupure < %ds)", prev_id, grace)
            return prev_id, True
    return create_conversation(agent="orchestrator"), False

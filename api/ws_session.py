"""État de session WebSocket partagé et reprise de conversation."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

import auth
import config
from api.middleware import _canonical_origin
from database import create_conversation
from websocket_registry import connected_ws

logger = logging.getLogger("jarvis")


_ws_last_session: dict[str, Any] = {"conversation_id": None, "closed_at": 0.0, "ws": None}


def resolve_websocket_auth(ws: WebSocket) -> tuple[Any, dict | None]:
    """Résout la session cookie ou le Bearer mobile Companion (jamais en query).

    Returns:
        (session, mobile_device) — au moins un des deux est non-None si l'appelant
        laisse passer la connexion. Les deux peuvent être None si non authentifié.
    """
    authorization = ws.headers.get("authorization") or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        mobile_device = auth.verify_mobile_token(token.strip())
        if mobile_device:
            return None, mobile_device
    cookie = ws.cookies.get(config.SESSION_COOKIE_NAME)
    if cookie and _websocket_cookie_origin_allowed(ws):
        session = auth.verify_session(cookie)
        if session:
            return session, None
    return None, None


def _websocket_cookie_origin_allowed(ws: WebSocket) -> bool:
    """Exige l'origine HTTP(S) exacte du handshake pour une session cookie.

    Le superviseur proxifie ce canal, et son client WebSocket réécrit le
    ``Host`` depuis l'URI de connexion : l'origine du navigateur arrivait donc
    avec l'hôte du backend, et la comparaison échouait toujours. Le proxy
    déclare la paire réelle dans des en-têtes dédiés, authentifiés par le jeton
    privé du superviseur ; la propriété vérifiée est la même, seule sa source
    change. Sans ce chemin, `/ws` refusait chaque connexion en 403 et le
    navigateur reconnectait sans fin.
    """
    from api.middleware import _proxied_websocket_origin_allowed

    if _proxied_websocket_origin_allowed(ws):
        return True

    candidate = _canonical_origin(ws.headers.get("origin") or "")
    host = ws.headers.get("host") or ""
    if not candidate or not host:
        return False
    if config.WEB_HTTPS_BEHIND_PROXY:
        public_scheme = "https"
    else:
        public_scheme = "https" if ws.url.scheme == "wss" else "http"
    expected = _canonical_origin(f"{public_scheme}://{host}")
    return expected is not None and candidate == expected


def websocket_confirmation_session_id(
    session: dict | None,
    mobile_device: dict | None,
) -> str:
    """Identité stable à laquelle lier les propositions de cette socket."""
    if mobile_device:
        return f"mobile:{mobile_device['device_id']}"
    if session:
        return f"session:{session['id']}"
    raise ValueError("WebSocket sans identité authentifiée")


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

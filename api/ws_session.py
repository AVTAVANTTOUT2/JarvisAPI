"""État de session WebSocket partagé et reprise de conversation."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

import auth
import config
from api.middleware import _canonical_origin
from database import (
    activate_profile,
    create_conversation,
    get_conversation_detail,
    normalize_profile_id,
    resolve_conversation_checkpoint,
    user_profile_exists,
)
from websocket_registry import connected_ws

logger = logging.getLogger("jarvis")


_ws_last_sessions: dict[str, dict[str, Any]] = {}


async def activate_websocket_profile(ws: WebSocket) -> bool:
    """Valide et active le profil isolé porté par le handshake."""
    try:
        profile_id = normalize_profile_id(
            ws.headers.get("x-jarvis-profile") or ws.cookies.get("jarvis_profile")
        )
    except ValueError:
        await ws.close(code=4400, reason="profil invalide")
        return False
    if not user_profile_exists(profile_id):
        await ws.close(code=4404, reason="profil introuvable")
        return False
    # Le ContextVar reste propre à la tâche WebSocket et à ses tâches filles.
    activate_profile(profile_id)
    return True


def remember_websocket_conversation(
    identity_key: str,
    ws: WebSocket,
    conversation_id: int,
    checkpoint_id: str,
    *,
    closed_at: float = 0.0,
) -> None:
    """Mémorise uniquement la conversation de cette identité authentifiée."""
    _ws_last_sessions[identity_key] = {
        "conversation_id": conversation_id,
        "checkpoint_id": checkpoint_id,
        "closed_at": closed_at,
        "ws": ws,
    }


def close_websocket_conversation(
    identity_key: str,
    ws: WebSocket,
    conversation_id: int,
    checkpoint_id: str,
) -> None:
    """Ouvre la fenêtre de grâce sans clôturer la conversation persistée."""
    import time

    remember_websocket_conversation(
        identity_key,
        ws,
        conversation_id,
        checkpoint_id,
        closed_at=time.time(),
    )


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
    from database import current_profile_id

    prefix = f"profile:{current_profile_id()}:"
    if mobile_device:
        return f"{prefix}mobile:{mobile_device['device_id']}"
    if session:
        return f"{prefix}session:{session['id']}"
    raise ValueError("WebSocket sans identité authentifiée")


def _resume_or_create_conversation(
    identity_key: str,
    requested_checkpoint_id: str | None = None,
    now: float | None = None,
) -> tuple[int, str, bool]:
    """Résout le checkpoint client ou reprend la session de cette identité.

    Retourne ``(conversation_id, checkpoint_id, resumed)``. L'ancien état
    global pouvait rattacher une socket à la conversation d'un autre client ;
    le fallback de grâce est désormais isolé par session ou appareil.

    Deux cas de reprise :
    - déconnexion détectée il y a moins de `grace` secondes ;
    - l'ancienne socket a déjà quitté `connected_ws` sans que sa clôture soit
      horodatée (coupure brutale, handler encore en cours) — même conversation.
    """
    import time as _time

    now = now or _time.time()
    grace = getattr(config, "VOICE_SESSION_GRACE_S", 180)
    if requested_checkpoint_id:
        conv_id, resumed = resolve_conversation_checkpoint(
            requested_checkpoint_id,
            agent="orchestrator",
            create=True,
        )
        detail = get_conversation_detail(conv_id)
        if not detail:
            raise LookupError("conversation checkpoint introuvable")
        return conv_id, str(detail["checkpoint_id"]), resumed

    previous = _ws_last_sessions.get(identity_key) or {}
    prev_id = previous.get("conversation_id")
    if prev_id:
        closed_at = previous.get("closed_at") or 0.0
        prev_ws = previous.get("ws")
        recently_closed = closed_at > 0.0 and (now - closed_at) < grace
        dropped = closed_at == 0.0 and prev_ws is not None and prev_ws not in connected_ws
        if recently_closed or dropped:
            detail = get_conversation_detail(int(prev_id))
            if detail and detail.get("checkpoint_id"):
                logger.info(
                    "[ws] Reprise de la conversation #%s pour %s (coupure < %ds)",
                    prev_id,
                    identity_key,
                    grace,
                )
                return int(prev_id), str(detail["checkpoint_id"]), True

    conv_id = create_conversation(agent="orchestrator")
    detail = get_conversation_detail(conv_id)
    if not detail or not detail.get("checkpoint_id"):
        raise RuntimeError("checkpoint de conversation non créé")
    return conv_id, str(detail["checkpoint_id"]), False

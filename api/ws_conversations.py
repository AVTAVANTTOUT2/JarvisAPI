"""Commandes WebSocket liées au cycle de vie d'une conversation."""

from __future__ import annotations

from typing import Any

from fastapi import WebSocket

from api.ws_session import remember_websocket_conversation
from database import (
    create_conversation,
    get_conversation_by_checkpoint,
    get_conversation_detail,
    resolve_conversation_checkpoint,
)


def _active_state(detail: dict[str, Any], *, resumed: bool = False) -> dict[str, Any]:
    checkpoint_id = detail.get("checkpoint_id")
    if not checkpoint_id:
        raise RuntimeError("checkpoint de conversation non créé")
    return {
        "conversation_id": int(detail["id"]),
        "checkpoint_id": str(checkpoint_id),
        "title": detail.get("title"),
        "resumed": resumed,
    }


def _remember(
    identity_key: str,
    ws: WebSocket,
    state: dict[str, Any],
) -> None:
    remember_websocket_conversation(
        identity_key,
        ws,
        int(state["conversation_id"]),
        str(state["checkpoint_id"]),
    )


def conversation_switched_payload(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "conversation_switched",
        "conversation_id": state["conversation_id"],
        "checkpoint_id": state["checkpoint_id"],
        "title": state.get("title"),
        "resumed": bool(state.get("resumed")),
    }


def create_websocket_conversation(
    identity_key: str,
    ws: WebSocket,
    requested_checkpoint_id: object = None,
) -> dict[str, Any]:
    """Crée un checkpoint ou rejoue idempotemment une création déjà reçue."""
    if requested_checkpoint_id is not None:
        conversation_id, resumed = resolve_conversation_checkpoint(
            str(requested_checkpoint_id),
            agent="orchestrator",
            create=True,
        )
    else:
        conversation_id = create_conversation(agent="orchestrator")
        resumed = False
    detail = get_conversation_detail(conversation_id)
    if not detail:
        raise RuntimeError("conversation non créée")
    state = _active_state(detail, resumed=resumed)
    _remember(identity_key, ws, state)
    return state


def switch_websocket_conversation(
    identity_key: str,
    ws: WebSocket,
    *,
    target_id: object = None,
    target_checkpoint_id: object = None,
) -> dict[str, Any]:
    """Résout une conversation existante et vérifie les deux identifiants."""
    has_id = isinstance(target_id, int) and not isinstance(target_id, bool)
    has_checkpoint = isinstance(target_checkpoint_id, str)
    if not has_id and not has_checkpoint:
        raise ValueError("conversation_id ou checkpoint_id manquant")

    if has_checkpoint:
        detail = get_conversation_by_checkpoint(str(target_checkpoint_id))
        if not detail:
            raise LookupError("Conversation introuvable")
        if has_id and int(detail["id"]) != int(target_id):
            raise ValueError("conversation_id et checkpoint_id incompatibles")
    else:
        detail = get_conversation_detail(int(target_id))
        if not detail:
            raise LookupError("Conversation introuvable")

    state = _active_state(detail, resumed=True)
    _remember(identity_key, ws, state)
    return state


def resume_message_checkpoint(
    identity_key: str,
    ws: WebSocket,
    current_conversation_id: int,
    message_checkpoint_id: object,
) -> dict[str, Any] | None:
    """Bascule avant traitement si le message désigne un autre checkpoint."""
    if message_checkpoint_id is None:
        return None
    if not isinstance(message_checkpoint_id, str):
        raise ValueError("Checkpoint de conversation invalide")
    conversation_id, _ = resolve_conversation_checkpoint(
        message_checkpoint_id,
        create=False,
    )
    if conversation_id == current_conversation_id:
        return None
    detail = get_conversation_detail(conversation_id)
    if not detail:
        raise LookupError("Checkpoint de conversation introuvable")
    state = _active_state(detail, resumed=True)
    _remember(identity_key, ws, state)
    return state

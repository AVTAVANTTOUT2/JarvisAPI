"""Chat texte mobile — création de conversation et envoi message (Bearer)."""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from api.chat_processing import _process_message_internal
from api.router_auth import _require_mobile_device
from database import (
    create_conversation,
    get_conversation_detail,
    get_mobile_chat_dedup,
    save_message,
    save_mobile_chat_dedup,
    update_conversation,
)

logger = logging.getLogger("jarvis.mobile_chat")

router = APIRouter()

_CLIENT_MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@router.post("/api/mobile/conversations")
async def api_mobile_create_conversation(request: Request, body: dict | None = None) -> dict:
    """Crée une conversation pour le Companion Android."""
    device = _require_mobile_device(request)
    payload = body or {}
    title = str(payload.get("title") or "").strip()[:200] or None
    conversation_id = create_conversation(agent="android_chat")
    if title:
        update_conversation(conversation_id, title=title)
    logger.info(
        "[mobile_chat] create conv=%s device=%s",
        conversation_id,
        device.get("device_id"),
    )
    return {
        "conversation_id": conversation_id,
        "title": title,
        "agent": "android_chat",
    }


@router.post("/api/mobile/chat")
async def api_mobile_chat(request: Request, body: dict) -> dict:
    """Envoi texte non-stream (fallback offline / WS indisponible).

    Idempotence : ``client_message_id`` + device_id → même réponse si rejoué.
    """
    device = _require_mobile_device(request)
    device_id = str(device["device_id"])
    content = str(body.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "content requis")
    if len(content) > 20_000:
        raise HTTPException(400, "content trop long (max 20000)")

    client_message_id = str(body.get("client_message_id") or "").strip() or None
    if client_message_id is not None and not _CLIENT_MESSAGE_ID_RE.match(client_message_id):
        raise HTTPException(400, "client_message_id invalide (8-64, alphanum/_/-)")

    if client_message_id:
        cached = get_mobile_chat_dedup(device_id, client_message_id)
        if cached is not None:
            logger.info(
                "[mobile_chat] idempotent hit device=%s id=%s",
                device_id,
                client_message_id,
            )
            return {**cached, "idempotent_replay": True}

    conversation_id = body.get("conversation_id")
    if conversation_id is not None:
        try:
            conversation_id = int(conversation_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "conversation_id invalide") from exc
        detail = get_conversation_detail(conversation_id)
        if not detail:
            raise HTTPException(404, "Conversation introuvable")
    else:
        conversation_id = create_conversation(agent="android_chat")

    try:
        save_message(conversation_id, "user", content)
    except Exception as exc:
        logger.exception("[mobile_chat] save user : %s", exc)
        raise HTTPException(500, "Impossible d'enregistrer le message") from exc

    result = await _process_message_internal(content, conversation_id, voice_mode=False)
    response_text = str(result.get("text") or "").strip()
    action = result.get("action")
    action_result = result.get("action_result")
    needs_confirmation = bool(
        action_result and action_result.get("needs_confirmation")
    )

    payload: dict[str, Any] = {
        "conversation_id": conversation_id,
        "response_text": response_text,
        "emotion": result.get("emotion") or "neutral",
        "agent": result.get("agent"),
        "model": result.get("model"),
        "cost": float(result.get("cost") or 0.0),
        "action": action,
        "action_result": action_result,
        "needs_confirmation": needs_confirmation,
        "client_message_id": client_message_id,
        "idempotent_replay": False,
    }

    if client_message_id:
        save_mobile_chat_dedup(device_id, client_message_id, conversation_id, payload)

    return payload


@router.post("/api/mobile/chat/confirm")
async def api_mobile_chat_confirm(request: Request, body: dict) -> dict:
    """Confirme ou refuse une action sensible proposée dans le chat."""
    _require_mobile_device(request)
    conversation_id = body.get("conversation_id")
    try:
        conversation_id = int(conversation_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "conversation_id requis") from exc
    if not get_conversation_detail(conversation_id):
        raise HTTPException(404, "Conversation introuvable")

    confirmed = bool(body.get("confirmed", False))
    if not confirmed:
        return {"ok": True, "cancelled": True, "conversation_id": conversation_id}

    # Réutilise le pipeline « oui » / confirmation textuelle.
    result = await _process_message_internal("oui", conversation_id, voice_mode=False)
    return {
        "ok": True,
        "cancelled": False,
        "conversation_id": conversation_id,
        "response_text": result.get("text") or "",
        "action_result": result.get("action_result"),
    }

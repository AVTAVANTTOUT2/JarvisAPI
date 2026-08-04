"""Chat texte mobile — création de conversation et envoi message (Bearer)."""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from actions import execute_action
from api.action_confirmations import (
    ProposalError,
    cancel_pending_proposal,
    consume_pending_proposal,
)
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
_PROPOSAL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


class _StrictMobileChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class MobileConversationCreateRequest(_StrictMobileChatRequest):
    title: str | None = Field(default=None, max_length=200)


class MobileChatRequest(_StrictMobileChatRequest):
    content: str = Field(min_length=1, max_length=20_000)
    conversation_id: int | None = Field(default=None, ge=1)
    client_message_id: str | None = Field(default=None, pattern=_CLIENT_MESSAGE_ID_RE)


class MobileChatConfirmationRequest(_StrictMobileChatRequest):
    conversation_id: int = Field(ge=1)
    proposal_id: str = Field(pattern=_PROPOSAL_ID_RE)
    confirmed: bool


@router.post("/api/mobile/conversations")
async def api_mobile_create_conversation(
    device: Annotated[dict[str, Any], Depends(_require_mobile_device)],
    body: MobileConversationCreateRequest | None = None,
) -> dict:
    """Crée une conversation pour le Companion Android."""
    title = (body.title or None) if body is not None else None
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
async def api_mobile_chat(
    body: MobileChatRequest,
    device: Annotated[dict[str, Any], Depends(_require_mobile_device)],
) -> dict:
    """Envoi texte non-stream (fallback offline / WS indisponible).

    Idempotence : ``client_message_id`` + device_id → même réponse si rejoué.
    """
    device_id = str(device["device_id"])
    content = body.content
    client_message_id = body.client_message_id

    if client_message_id:
        cached = get_mobile_chat_dedup(device_id, client_message_id)
        if cached is not None:
            logger.info(
                "[mobile_chat] idempotent hit device=%s id=%s",
                device_id,
                client_message_id,
            )
            return {**cached, "idempotent_replay": True}

    conversation_id = body.conversation_id
    if conversation_id is not None:
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

    result = await _process_message_internal(
        content,
        conversation_id,
        voice_mode=False,
        confirmation_session_id=f"mobile:{device_id}",
    )
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
async def api_mobile_chat_confirm(
    body: MobileChatConfirmationRequest,
    device: Annotated[dict[str, Any], Depends(_require_mobile_device)],
) -> dict:
    """Confirme ou refuse une action sensible proposée dans le chat."""
    confirmation_session_id = f"mobile:{device['device_id']}"
    conversation_id = body.conversation_id
    if not get_conversation_detail(conversation_id):
        raise HTTPException(404, "Conversation introuvable")

    confirmed = body.confirmed
    proposal_id = body.proposal_id
    if not confirmed:
        cancelled = cancel_pending_proposal(
            proposal_id,
            conversation_id=conversation_id,
            session_id=confirmation_session_id,
        )
        return {
            "ok": cancelled,
            "cancelled": cancelled,
            "conversation_id": conversation_id,
        }

    try:
        action = consume_pending_proposal(
            proposal_id,
            conversation_id=conversation_id,
            session_id=confirmation_session_id,
        )
    except ProposalError as exc:
        raise HTTPException(409, str(exc)) from exc
    action_result = await execute_action(action)
    response_text = str(action_result.get("message") or "Action exécutée.")
    try:
        save_message(conversation_id, "assistant", response_text, agent="action_executor")
    except Exception as exc:
        logger.debug("[mobile_chat] save confirmation : %s", exc)
    return {
        "ok": bool(action_result.get("ok")),
        "cancelled": False,
        "conversation_id": conversation_id,
        "response_text": response_text,
        "action_type": action.get("type"),
        "action_result": action_result,
    }

"""Décisions de confirmation d'action reçues par WebSocket."""

from __future__ import annotations

import logging

from fastapi import WebSocket

from actions import execute_action
from agents.display_text import finalize_assistant_display_text
from agents.orchestrator import orchestrator
from api.action_confirmations import (
    ProposalError,
    cancel_pending_proposal,
    consume_pending_proposal,
    is_valid_proposal_id,
)
from api.chat_actions import ACTIONS_WITH_FOLLOWUP, _format_action_result_for_followup
from api.chat_context import prepare_turn
from api.llm_logging import _schedule_llm_log
from database import save_message

logger = logging.getLogger("jarvis")


async def handle_ws_action_decision(
    ws: WebSocket,
    message: dict,
    *,
    conversation_id: int,
    confirmation_session_id: str,
) -> bool:
    """Traite confirm/cancel ; retourne False pour un autre type de message."""
    message_type = message.get("type")
    if message_type not in {"action_confirm", "action_cancel"}:
        return False
    proposal_id = message.get("proposal_id")
    if set(message) != {"type", "proposal_id"} or not is_valid_proposal_id(proposal_id):
        await ws.send_json({"type": "error", "message": "proposal_id invalide"})
        return True

    if message_type == "action_cancel":
        cancelled = cancel_pending_proposal(
            proposal_id,
            conversation_id=conversation_id,
            session_id=confirmation_session_id,
        )
        await ws.send_json({"type": "action_cancelled", "cancelled": cancelled})
        return True

    try:
        action = consume_pending_proposal(
            proposal_id,
            conversation_id=conversation_id,
            session_id=confirmation_session_id,
        )
    except ProposalError as exc:
        await ws.send_json({"type": "error", "message": str(exc)})
        return True

    action_subject = next(
        (
            str(action.get(key) or "").strip()
            for key in ("query", "title", "summary", "recipient", "name")
            if action.get(key)
        ),
        "",
    )
    turn_query = " ".join(
        part for part in (str(action.get("type") or "action"), action_subject) if part
    )[:1000]
    snapshot = await prepare_turn(
        turn_query,
        conversation_id,
        interaction_mode="stream",
    )
    turn_context = snapshot.to_context()
    knowledge = snapshot.public_payload()

    _schedule_llm_log(
        agent="orchestrator",
        action_type=str(action.get("type") or "unknown"),
        payload={"conversation_id": conversation_id, "action": action},
        status="pending",
    )
    try:
        result = await execute_action(action)
    except Exception as exc:
        logger.exception("action_confirm : %s", exc)
        result = {"ok": False, "message": str(exc)}
    await ws.send_json(
        {
            "type": "action_result",
            "action": action.get("type"),
            "result": result,
            "knowledge": knowledge,
        }
    )

    if (
        result.get("ok")
        and action.get("type") in ACTIONS_WITH_FOLLOWUP
        and not result.get("needs_confirmation")
    ):
        await _send_safe_followup(
            ws,
            action,
            result,
            conversation_id,
            context=turn_context,
            knowledge=knowledge,
        )
    return True


async def _send_safe_followup(
    ws: WebSocket,
    action: dict,
    result: dict,
    conversation_id: int,
    *,
    context: dict,
    knowledge: dict,
) -> None:
    try:
        payload = _format_action_result_for_followup(action, result)
        await ws.send_json({"type": "status", "content": "Synthèse du résultat…"})
        followup = await orchestrator.handle(
            (
                f"Résultat local filtré de l'action :\n\n{payload}\n\n"
                "L'utilisateur a confirmé l'exécution. Résume le résultat clairement. "
                "Pas de bloc action."
            ),
            conversation_id=conversation_id,
            voice_mode=False,
            context=context,
        )
        text = finalize_assistant_display_text(followup.get("response", ""))
        await ws.send_json(
            {"type": "response_followup", "content": text, "knowledge": knowledge}
        )
        save_message(
            conversation_id,
            "assistant",
            text,
            agent=followup.get("agent"),
            model=followup.get("model"),
            tokens_in=int(followup.get("tokens_in") or 0),
            tokens_out=int(followup.get("tokens_out") or 0),
            cost=float(followup.get("cost") or 0.0),
        )
    except Exception as exc:
        logger.exception("[action_confirm] followup : %s", exc)

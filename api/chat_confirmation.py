"""Résolution des confirmations d'action du pipeline conversationnel interne."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("jarvis")


async def resolve_pending_confirmation(
    original_text: str,
    conversation_id: int,
    *,
    confirmation_session_id: str,
    voice_mode: bool,
    persist_assistant: bool,
    trace: Any | None,
    execute_action_fn: Callable[..., Any],
    orchestrator_handle_fn: Callable[..., Any],
    save_message_fn: Callable[..., Any],
    update_conversation_activity_fn: Callable[..., Any],
    mark_voice_trace_fn: Callable[..., Any],
    actions_with_followup: Any,
    peek_pending_proposal_fn: Callable[..., Any],
    pop_pending_action_fn: Callable[..., Any],
    imperative_confirmation_fn: Callable[..., bool],
    unmatched_confirmation_reply_fn: Callable[..., dict[str, Any]],
    format_action_result_for_followup_fn: Callable[..., str],
    finalize_assistant_display_text_fn: Callable[..., str],
) -> dict[str, Any] | None:
    """Exécute une confirmation en attente ou répond à une confirmation orpheline."""

    pending_action = peek_pending_proposal_fn(
        conversation_id=conversation_id,
        session_id=confirmation_session_id,
    )
    confirmed_action = pop_pending_action_fn(
        original_text,
        conversation_id,
        confirmation_session_id,
    )
    if confirmed_action is not None:
        mark_voice_trace_fn(
            trace,
            "ACTION_STARTED",
            action_type=confirmed_action.get("type") or "?",
        )
        try:
            action_result = await execute_action_fn(confirmed_action)
        except Exception as exc:
            logger.exception("[internal-pending] execute_action : %s", exc)
            action_result = {"ok": False, "message": str(exc)}
        mark_voice_trace_fn(
            trace,
            "ACTION_COMPLETED",
            action_type=confirmed_action.get("type") or "?",
            ok=bool(action_result.get("ok")),
        )

        display_text = str(action_result.get("message", "Action exécutée."))
        emotion = "neutral"
        final_meta: dict[str, Any] = {
            "agent": "orchestrator",
            "model": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "cost": 0.0,
        }

        if (
            action_result.get("ok")
            and not action_result.get("needs_confirmation")
            and confirmed_action.get("type") in actions_with_followup
        ):
            try:
                payload = format_action_result_for_followup_fn(
                    confirmed_action, action_result
                )
                followup = await orchestrator_handle_fn(
                    (
                        f"Résultat brut de l'action :\n\n{payload}\n\n"
                        f"Question originale : {original_text}\n\n"
                        "Résume ce résultat de façon claire et utile. Pas de bloc action."
                    ),
                    conversation_id=conversation_id,
                    voice_mode=voice_mode,
                )
                emotion = followup.get("emotion", emotion)
                display_text = finalize_assistant_display_text_fn(
                    followup.get("response", display_text)
                )
                final_meta = followup
            except Exception as exc:
                logger.exception("[internal-pending-followup] %s", exc)

        display_text = (
            re.sub(
                r"```(?:json|action|save)\s*\{[\s\S]*?\}\s*```", "", display_text
            ).strip()
            or display_text
        )

        if persist_assistant:
            try:
                save_message_fn(
                    conversation_id,
                    "assistant",
                    display_text,
                    agent=final_meta.get("agent"),
                    model=final_meta.get("model"),
                    tokens_in=final_meta.get("tokens_in", 0),
                    tokens_out=final_meta.get("tokens_out", 0),
                    cost=final_meta.get("cost", 0.0),
                )
            except Exception as exc:
                logger.error("[internal-pending] save assistant : %s", exc)

        return {
            "text": display_text,
            "emotion": emotion,
            "action": pending_action,
            "action_result": action_result,
            "agent": final_meta.get("agent"),
            "model": final_meta.get("model"),
            "cost": float(final_meta.get("cost") or 0.0),
        }

    if not imperative_confirmation_fn(original_text):
        return None

    reply = unmatched_confirmation_reply_fn()
    if persist_assistant:
        try:
            save_message_fn(
                conversation_id,
                "assistant",
                reply["text"],
                agent="orchestrator",
                tokens_in=0,
                tokens_out=0,
                cost=0.0,
            )
        except Exception as exc:
            logger.error("[internal-confirmation] save assistant : %s", exc)
    try:
        update_conversation_activity_fn(conversation_id)
    except Exception:
        pass
    return reply

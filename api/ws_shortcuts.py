"""Branches courtes exécutées avant l'orchestration principale WebSocket."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("jarvis")


async def handle_loop_or_quick_reply(
    ws: WebSocket,
    original_text: str,
    conversation_id: int,
    *,
    voice_mode: bool,
    send_tts: bool,
    confirmation_session_id: str,
    client_message_id: str | None,
    extra_context: dict[str, Any],
    agentic_context: dict[str, str | None] | None,
    config_module: Any,
    parse_loop_command_fn: Callable[..., Any],
    maybe_send_agentic_run_fn: Callable[..., Any],
    agentic_idempotency_key_fn: Callable[..., str],
    run_loop_mode_ws_fn: Callable[..., Any],
    save_message_fn: Callable[..., Any],
    easter_egg_match_fn: Callable[..., Any],
    send_tts_streaming_fn: Callable[..., Any],
) -> dict[str, Any] | None:
    """Traite `/loop`, la répétition TTS et les easter eggs."""

    loop_task = parse_loop_command_fn(original_text)
    if loop_task is not None:
        if not loop_task.strip():
            await ws.send_json(
                {
                    "type": "error",
                    "message": "Usage : /loop [tâche à accomplir autonomement]",
                }
            )
            return {"emotion": "neutral", "response": ""}
        agentic = await maybe_send_agentic_run_fn(
            ws,
            f"/agent {loop_task.strip()}",
            conversation_id,
            voice_mode=voice_mode,
            send_tts=send_tts,
            idempotency_key=agentic_idempotency_key_fn(
                confirmation_session_id, client_message_id
            ),
            enriched_context=extra_context,
            **(agentic_context or {}),
        )
        if agentic is not None:
            return agentic
        if (
            str(getattr(config_module, "AGENTIC_RUNTIME_FALLBACK", "disabled")).lower()
            == "legacy"
        ):
            return await run_loop_mode_ws_fn(
                ws,
                loop_task.strip(),
                conversation_id,
                voice_mode=voice_mode,
                confirmation_session_id=confirmation_session_id,
                context=extra_context,
            )
        await ws.send_json({"type": "error", "message": "Runtime agentique désactivé"})
        return {"emotion": "neutral", "response": "Runtime agentique désactivé"}

    from audio.tts_cache import is_repeat_request, last_tts

    if is_repeat_request(original_text):
        entry = last_tts.get()
        if entry:
            try:
                save_message_fn(
                    conversation_id, "assistant", entry["text"], agent="jarvis"
                )
            except Exception as exc:
                logger.error("save répète : %s", exc)
            await ws.send_json(
                {
                    "type": "response",
                    "agent": "jarvis",
                    "content": entry["text"],
                    "emotion": entry["emotion"],
                    "model": "replay",
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cost": 0.0,
                }
            )
            if send_tts:
                await ws.send_json(
                    {
                        "type": "speaking",
                        "emotion": entry["emotion"],
                        "audio_mime": entry.get("mime", "audio/mpeg"),
                    }
                )
                await ws.send_bytes(entry["audio"])
                await ws.send_json({"type": "speech_done"})
            return {"emotion": entry["emotion"], "response": entry["text"]}

    egg = easter_egg_match_fn(original_text)
    if egg is None:
        return None
    egg_text = egg["response"]
    egg_emotion = egg["emotion"]
    try:
        save_message_fn(conversation_id, "assistant", egg_text, agent="jarvis")
    except Exception as exc:
        logger.error("save easter egg : %s", exc)
    await ws.send_json(
        {
            "type": "response",
            "agent": "jarvis",
            "content": egg_text,
            "emotion": egg_emotion,
            "model": "easter-egg",
            "tokens_in": 0,
            "tokens_out": 0,
            "cost": 0.0,
        }
    )
    if send_tts:
        await send_tts_streaming_fn(ws, egg_text, egg_emotion)
    return {"emotion": egg_emotion, "response": egg_text}


async def handle_pending_action(
    ws: WebSocket,
    original_text: str,
    conversation_id: int,
    *,
    confirmation_session_id: str,
    voice_mode: bool,
    actions_with_followup: Any,
    peek_pending_proposal_fn: Callable[..., Any],
    check_pending_proposal_fn: Callable[..., Any],
    format_action_result_for_followup_fn: Callable[..., str],
    orchestrator_handle_fn: Callable[..., Any],
    finalize_assistant_display_text_fn: Callable[..., str],
) -> dict[str, Any] | None:
    """Exécute une proposition confirmée avant tout nouvel appel principal au LLM."""

    pending_action = peek_pending_proposal_fn(
        conversation_id=conversation_id,
        session_id=confirmation_session_id,
    )
    pending_action_type = pending_action.get("type") if pending_action else None
    pending_result = await check_pending_proposal_fn(
        ws,
        original_text,
        conversation_id,
        confirmation_session_id,
    )
    if pending_result is None:
        return None

    await ws.send_json(
        {
            "type": "action_result",
            "action": pending_action_type or "?",
            "action_payload": pending_action,
            "result": pending_result,
        }
    )
    display_text = str(pending_result.get("message") or "Action exécutée.")
    emotion = "neutral"
    followup_action = pending_action or {"type": pending_action_type or "unknown"}
    if (
        pending_result.get("ok")
        and not pending_result.get("needs_confirmation")
        and followup_action.get("type") in actions_with_followup
    ):
        try:
            payload = format_action_result_for_followup_fn(
                followup_action, pending_result
            )
        except Exception:
            payload = "Résultat indisponible à la frontière LLM."
        followup = await orchestrator_handle_fn(
            (
                f"Résultat de l'action exécutée :\n\n{payload}\n\n"
                f"Question originale : {original_text}\n\n"
                "Résume ce résultat pour l'utilisateur de façon concise."
            ),
            conversation_id=conversation_id,
            voice_mode=voice_mode,
        )
        display_text = finalize_assistant_display_text_fn(followup.get("response", ""))
        emotion = followup.get("emotion", "neutral")
        await ws.send_json({"type": "response_followup", "content": display_text})
    return {
        "emotion": emotion,
        "response": display_text or str(pending_result.get("message", "")),
    }

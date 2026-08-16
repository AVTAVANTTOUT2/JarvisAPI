"""Traitement conversationnel associé à une socket active."""

from __future__ import annotations

import logging

from fastapi import WebSocket

import config
from actions import execute_action
from agents import easter_eggs, get_agent
from agents.autonomous_loop import parse_loop_command
from agents.display_text import (
    finalize_assistant_display_text,
    sanitize_streaming_display,
)
from agents.orchestrator import orchestrator
from api.chat_actions import (
    ACTIONS_WITH_FOLLOWUP,
    _check_pending_proposal,
    _extract_action_from_text,
    _format_action_result_for_followup,
    _is_agentic_action,
    _maybe_store_pending_proposal,
    _run_loop_mode_ws,
    _should_defer_action,
)
from api.action_confirmations import peek_pending_proposal
from api.chat_context import (
    _build_enriched_context,
    _send_tts_streaming,
)
from api.conversation_titles import notify_and_schedule_conversation_title
from api.llm_logging import _schedule_llm_log
from api.ws_agentic import (
    agentic_idempotency_key,
    maybe_send_agentic_run,
    maybe_send_legacy_delegation,
)
from api.ws_shortcuts import handle_loop_or_quick_reply, handle_pending_action
from database import save_message, update_conversation_activity

logger = logging.getLogger("jarvis")


async def _process_message(
    ws: WebSocket,
    content: str,
    conversation_id: int,
    *,
    voice_mode: bool = False,
    stream: bool = True,
    send_tts: bool = False,
    confirmation_session_id: str,
    client_message_id: str | None = None,
    agentic_context: dict[str, str | None] | None = None,
) -> dict:
    """Pipeline unique texte + vocal : DB → orchestrateur (même enrichissement) →
    nettoyage affichage → actions → TTS optionnel.

    ``voice_mode=True`` : pas de streaming ; ``orchestrator.handle(..., voice_mode=True)``
    (préfixe ``[VOICE_MODE]``, Haiku + tokens courts via agents).

    Retourne ``{emotion, response}`` (``response`` = texte affichable nettoyé).
    """
    try:
        if voice_mode:
            stream = False

        original_text = content

        try:
            interaction_mode = "voice" if voice_mode else "stream" if stream else "chat"
            extra_context = await _build_enriched_context(
                content,
                conversation_id,
                interaction_mode=interaction_mode,
            )
        except Exception as e:
            logger.warning("[_process_message] _build_enriched_context : %s", e)
            extra_context = {}

        extra_context["__defer_persist"] = True

        if "documents_context" in extra_context:
            content = extra_context.pop("documents_context") + "\n\n" + content

        try:
            save_message(conversation_id, "user", original_text)
        except Exception as e:
            logger.error("Erreur save user message : %s", e)

        try:
            update_conversation_activity(conversation_id)
        except Exception as e:
            logger.debug("[conv] update_activity user : %s", e)

        quick_reply = await handle_loop_or_quick_reply(
            ws,
            original_text,
            conversation_id,
            voice_mode=voice_mode,
            send_tts=send_tts,
            confirmation_session_id=confirmation_session_id,
            client_message_id=client_message_id,
            extra_context=extra_context,
            agentic_context=agentic_context,
            config_module=config,
            parse_loop_command_fn=parse_loop_command,
            maybe_send_agentic_run_fn=maybe_send_agentic_run,
            agentic_idempotency_key_fn=agentic_idempotency_key,
            run_loop_mode_ws_fn=_run_loop_mode_ws,
            save_message_fn=save_message,
            easter_egg_match_fn=easter_eggs.match,
            send_tts_streaming_fn=_send_tts_streaming,
        )
        if quick_reply is not None:
            return quick_reply

        agentic = await maybe_send_agentic_run(
            ws,
            original_text,
            conversation_id,
            voice_mode=voice_mode,
            send_tts=send_tts,
            idempotency_key=agentic_idempotency_key(
                confirmation_session_id, client_message_id
            ),
            enriched_context=extra_context,
            **(agentic_context or {}),
        )
        if agentic is not None:
            return agentic

        legacy = await maybe_send_legacy_delegation(
            ws,
            original_text,
            conversation_id,
            voice_mode=voice_mode,
            send_tts=send_tts,
            confirmation_session_id=confirmation_session_id,
        )
        if legacy is not None:
            return legacy

        pending_response = await handle_pending_action(
            ws,
            original_text,
            conversation_id,
            confirmation_session_id=confirmation_session_id,
            voice_mode=voice_mode,
            actions_with_followup=ACTIONS_WITH_FOLLOWUP,
            peek_pending_proposal_fn=peek_pending_proposal,
            check_pending_proposal_fn=_check_pending_proposal,
            format_action_result_for_followup_fn=_format_action_result_for_followup,
            orchestrator_handle_fn=orchestrator.handle,
            finalize_assistant_display_text_fn=finalize_assistant_display_text,
        )
        if pending_response is not None:
            return pending_response

        full_response = ""
        final_meta: dict = {}
        emotion = "neutral"
        pending_done: dict | None = None
        stream_clean_sent = ""

        if stream:
            async for event in orchestrator.handle_stream(
                content,
                conversation_id=conversation_id,
                voice_mode=False,
                context=extra_context,
            ):
                if event.get("type") == "done":
                    pending_done = event
                    final_meta = event
                    emotion = event.get("emotion", "neutral")
                    continue
                if event.get("type") == "chunk":
                    full_response += event["content"]
                    clean_now = sanitize_streaming_display(full_response)
                    delta = clean_now[len(stream_clean_sent) :]
                    stream_clean_sent = clean_now
                    if delta:
                        await ws.send_json({"type": "chunk", "content": delta})
                    continue
                await ws.send_json(event)
        else:
            result = await orchestrator.handle(
                content,
                conversation_id=conversation_id,
                voice_mode=voice_mode,
                context=extra_context,
            )
            full_response = result["response"]
            emotion = result.get("emotion", "neutral")
            final_meta = result
            display_ns = finalize_assistant_display_text(full_response)
            await ws.send_json(
                {
                    "type": "response",
                    "agent": result["agent"],
                    "category": result.get("category"),
                    "content": display_ns,
                    "model": result["model"],
                    "tokens_in": result["tokens_in"],
                    "tokens_out": result["tokens_out"],
                    "cost": result["cost"],
                    "emotion": emotion,
                }
            )

        raw_accumulated = full_response
        action, after_action = _extract_action_from_text(raw_accumulated)
        display_text = finalize_assistant_display_text(after_action)

        if stream:
            await ws.send_json(
                {"type": "response_clean", "content": display_text or ""}
            )
            if pending_done is not None:
                await ws.send_json(pending_done)
        elif display_text != full_response:
            await ws.send_json({"type": "response_clean", "content": display_text})

        action_result: dict | None = None
        if action:
            _schedule_llm_log(
                agent=str(final_meta.get("agent") or "orchestrator"),
                action_type=str(action.get("type") or "unknown"),
                payload={"conversation_id": conversation_id, "action": action},
                status="pending",
            )

            if _is_agentic_action(action):
                agent_name = final_meta.get("agent", "orchestrator")
                agent = get_agent(agent_name) or orchestrator
                logger.info(
                    "[agentic] Démarrage boucle agentique pour %s", action.get("type")
                )

                await ws.send_json(
                    {
                        "type": "status",
                        "content": "Mode agent activé — exécution en cours…",
                    }
                )

                try:
                    loop_result = await agent._run_agentic_loop(
                        user_message=original_text,
                        conversation_id=conversation_id,
                        context=extra_context,
                        initial_action=action,
                    )
                except Exception as e:
                    logger.exception("[agentic] boucle : %s", e)
                    loop_result = {
                        "results": [
                            {
                                "step": 1,
                                "action": action,
                                "result": {"ok": False, "message": str(e)},
                            }
                        ],
                        "step_count": 1,
                        "final_status": "failed",
                    }

                await ws.send_json(
                    {
                        "type": "agentic_result",
                        "steps": loop_result.get("step_count", 0),
                        "status": loop_result.get("final_status", "completed"),
                    }
                )

                results_text = "\n".join(
                    [
                        f"Étape {r['step']}: "
                        f"{str(r['result'].get('output', r['result'].get('message', '')))[:1000]}"
                        for r in loop_result.get("results", [])
                        if isinstance(r.get("step"), int)
                    ]
                )

                action_result = {
                    "ok": loop_result.get("final_status") != "failed",
                    "output": results_text,
                    "agentic": True,
                }

                if results_text:
                    safe_results = _format_action_result_for_followup(
                        {"type": "terminal"},
                        action_result,
                    )
                    fu = await orchestrator.handle(
                        (
                            f"Résultats des actions exécutées :\n\n{safe_results}\n\n"
                            f"Question originale : {original_text}\n\n"
                            "Synthétise ces résultats de façon claire et utile."
                        ),
                        conversation_id=conversation_id,
                        voice_mode=voice_mode,
                    )
                    display_text = finalize_assistant_display_text(
                        fu.get("response", display_text)
                    )
                    emotion = fu.get("emotion", emotion)
                    final_meta = fu
                    await ws.send_json(
                        {
                            "type": "response_followup",
                            "content": display_text,
                        }
                    )
            else:
                if _should_defer_action(display_text, action):
                    pending_client_action = _maybe_store_pending_proposal(
                        action,
                        conversation_id,
                        confirmation_session_id,
                    )
                    action_result = {
                        "ok": True,
                        "deferred": True,
                        "message": display_text,
                    }
                    await ws.send_json(
                        {
                            "type": "action_pending",
                            "action": pending_client_action,
                            "action_type": action.get("type"),
                            "message": display_text,
                        }
                    )
                    logger.info("[pending] Action différée (proposition utilisateur)")
                else:
                    if action.get("type") == "mail" and not action.get("confirmed"):
                        _maybe_store_pending_proposal(
                            action,
                            conversation_id,
                            confirmation_session_id,
                        )
                        logger.info(
                            "[pending] Proposition mail stockée pour confirmation"
                        )

                    try:
                        action_result = await execute_action(action)
                        pending_client_action = None
                        if action_result.get("needs_confirmation"):
                            pending_client_action = _maybe_store_pending_proposal(
                                action,
                                conversation_id,
                                confirmation_session_id,
                            )
                        await ws.send_json(
                            {
                                "type": "action_result",
                                "action": action.get("type"),
                                "action_payload": pending_client_action or action,
                                "result": action_result,
                            }
                        )
                        if action_result.get("needs_confirmation"):
                            logger.info(
                                "[pending] Action %s en attente de confirmation",
                                action.get("type"),
                            )
                        logger.info(
                            "[action] %s → ok=%s",
                            action.get("type"),
                            action_result.get("ok"),
                        )
                    except Exception as e:
                        logger.exception("[action] execute_action exception : %s", e)
                        action_result = {"ok": False, "message": str(e)}
                        await ws.send_json(
                            {
                                "type": "action_result",
                                "action": action.get("type"),
                                "action_payload": action,
                                "result": action_result,
                            }
                        )

                if (
                    action_result
                    and not (
                        action_result.get("deferred")
                        or action_result.get("needs_confirmation")
                    )
                    and action.get("type") in ACTIONS_WITH_FOLLOWUP
                    and action_result.get("ok")
                ):
                    try:
                        payload = _format_action_result_for_followup(
                            action, action_result
                        )
                        await ws.send_json(
                            {
                                "type": "status",
                                "content": "Synthèse du résultat…",
                            }
                        )
                        fu = await orchestrator.handle(
                            (
                                f"Résultat brut de l'action :\n\n{payload}\n\n"
                                f"Question originale : {original_text}\n\n"
                                "Résume ce résultat de façon claire et utile pour l'utilisateur. "
                                "Pas de bloc action."
                            ),
                            conversation_id=conversation_id,
                            voice_mode=voice_mode,
                        )
                        display_text = finalize_assistant_display_text(
                            fu.get("response", "")
                        )
                        emotion = fu.get("emotion", emotion)
                        final_meta = {
                            "agent": fu.get("agent", final_meta.get("agent")),
                            "model": fu.get("model", final_meta.get("model")),
                            "tokens_in": int(fu.get("tokens_in") or 0),
                            "tokens_out": int(fu.get("tokens_out") or 0),
                            "cost": float(fu.get("cost") or 0.0),
                        }
                        await ws.send_json(
                            {
                                "type": "response_followup",
                                "content": display_text,
                            }
                        )
                    except Exception as e:
                        logger.exception(
                            "[followup] action %s : %s", action.get("type"), e
                        )

        if raw_accumulated:
            try:
                save_message(
                    conversation_id,
                    "assistant",
                    display_text,
                    agent=final_meta.get("agent"),
                    model=final_meta.get("model"),
                    tokens_in=final_meta.get("tokens_in", 0),
                    tokens_out=final_meta.get("tokens_out", 0),
                    cost=final_meta.get("cost", 0.0),
                    usage_estimated=final_meta.get("usage_estimated", False),
                )
            except Exception as e:
                logger.error("Erreur save assistant message : %s", e)

        try:
            update_conversation_activity(conversation_id)
        except Exception as e:
            logger.debug("[conv] update_activity assistant : %s", e)

        try:
            await notify_and_schedule_conversation_title(ws, conversation_id)
        except Exception as e:
            logger.debug("[conv] conversation_updated event : %s", e)

        tts_text = display_text.strip() if display_text else ""
        if send_tts and tts_text:
            await _send_tts_streaming(ws, tts_text, emotion)

        return {"emotion": emotion, "response": display_text}
    except Exception as e:
        logger.exception("_process_message : %s", e)
        detail = f"{type(e).__name__}: {e}"[:200]
        try:
            await ws.send_json(
                {
                    "type": "error",
                    "message": f"Erreur lors du traitement du message ({detail}).",
                }
            )
        except Exception:
            pass
        return {"emotion": "neutral", "response": ""}

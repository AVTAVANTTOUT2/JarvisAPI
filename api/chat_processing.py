"""Pipeline interne de traitement des messages sans transport WebSocket."""

from __future__ import annotations

import asyncio
import logging
import re

import config
from actions import execute_action
from agents import get_agent
from agents.autonomous_loop import parse_loop_command
from agents.display_text import extract_leading_emotion, finalize_assistant_display_text
from agents.orchestrator import orchestrator
from api.chat_actions import (
    ACTIONS_WITH_FOLLOWUP,
    _extract_action_from_text,
    _format_action_result_for_followup,
    _is_agentic_action,
    _maybe_store_pending_proposal,
    _pop_pending_action_if_confirmed,
    _run_loop_mode_internal,
    _should_defer_action,
)
from api.chat_context import _build_enriched_context, _maybe_title_conversation
from api.llm_logging import _schedule_llm_log
from database import save_message, update_conversation_activity

logger = logging.getLogger("jarvis")


async def _process_message_internal(
    text: str,
    conversation_id: int,
    voice_mode: bool = False,
) -> dict:
    """Pipeline JARVIS sans WebSocket — pour les endpoints REST (journal, contacts, etc.).

    Applique le même enrichissement de contexte que _process_message, appelle l'orchestrateur,
    exécute les actions avec 2e passe si nécessaire, sauvegarde le message assistant.

    Retourne {text, emotion, action, action_result, agent, model, cost}.
    """
    try:
        jarvis_patterns = (
            "noté, monsieur",
            "ajouté à l'agenda",
            "bien noté",
            "je m'en occupe",
        )
        if isinstance(text, str) and any(text.strip().lower().startswith(p) for p in jarvis_patterns):
            logger.warning("[anti-loop] Message ignoré (ressemble à une réponse JARVIS): %s", text[:80])
            return {
                "text": "",
                "emotion": "neutral",
                "action": None,
                "action_result": None,
                "agent": "none",
                "model": None,
                "cost": 0.0,
            }

        original_text = text
        # ── Mode autonome /loop ──
        loop_task = parse_loop_command(original_text)
        if loop_task is not None:
            if not loop_task.strip():
                return {
                    "text": "Usage : /loop [tâche à accomplir autonomement]",
                    "emotion": "neutral",
                    "action": None,
                    "action_result": None,
                    "agent": "loop",
                    "model": config.LOOP_MODEL,
                    "cost": 0.0,
                }
            try:
                save_message(conversation_id, "user", original_text)
            except Exception as exc:
                logger.debug("[loop] save user internal : %s", exc)
            return await _run_loop_mode_internal(
                loop_task.strip(),
                conversation_id,
                voice_mode=voice_mode,
            )

        # Confirmation « oui / vas-y » sur une action en attente (REST)
        pending_action = _pop_pending_action_if_confirmed(original_text, conversation_id)
        if pending_action is not None:
            try:
                action_result = await execute_action(pending_action)
            except Exception as e:
                logger.exception("[internal-pending] execute_action : %s", e)
                action_result = {"ok": False, "message": str(e)}

            display_text = str(action_result.get("message", "Action exécutée."))
            emotion = "neutral"
            final_meta: dict = {
                "agent": "orchestrator",
                "model": None,
                "tokens_in": 0,
                "tokens_out": 0,
                "cost": 0.0,
            }

            if (
                action_result.get("ok")
                and not action_result.get("needs_confirmation")
                and pending_action.get("type") in ACTIONS_WITH_FOLLOWUP
            ):
                try:
                    payload = _format_action_result_for_followup(pending_action, action_result)
                    fu = await orchestrator.handle(
                        (
                            f"Résultat brut de l'action :\n\n{payload}\n\n"
                            f"Question originale : {original_text}\n\n"
                            "Résume ce résultat de façon claire et utile. Pas de bloc action."
                        ),
                        conversation_id=conversation_id,
                        voice_mode=voice_mode,
                    )
                    emotion = fu.get("emotion", emotion)
                    display_text = finalize_assistant_display_text(fu.get("response", display_text))
                    final_meta = fu
                except Exception as e:
                    logger.exception("[internal-pending-followup] %s", e)

            display_text = re.sub(
                r'```(?:json|action|save)\s*\{[\s\S]*?\}\s*```', '', display_text
            ).strip() or display_text

            try:
                save_message(
                    conversation_id, "assistant", display_text,
                    agent=final_meta.get("agent"),
                    model=final_meta.get("model"),
                    tokens_in=final_meta.get("tokens_in", 0),
                    tokens_out=final_meta.get("tokens_out", 0),
                    cost=final_meta.get("cost", 0.0),
                )
            except Exception as e:
                logger.error("[internal-pending] save assistant : %s", e)

            return {
                "text": display_text,
                "emotion": emotion,
                "action": pending_action,
                "action_result": action_result,
                "agent": final_meta.get("agent"),
                "model": final_meta.get("model"),
                "cost": float(final_meta.get("cost") or 0.0),
            }

        context = await _build_enriched_context(text, conversation_id)

        if voice_mode:
            context["voice_mode"] = True

        if "documents_context" in context:
            text = context.pop("documents_context") + "\n\n" + text

        result = await orchestrator.handle(
            text, conversation_id=conversation_id, voice_mode=voice_mode, context=context
        )
        full_response = result.get("response", "")
        emotion_raw, _ = extract_leading_emotion(full_response)
        emotion = emotion_raw or result.get("emotion", "neutral")

        action, after_action = _extract_action_from_text(full_response)
        display_text = finalize_assistant_display_text(after_action)

        action_result: dict | None = None
        final_meta = result

        if action:
            _schedule_llm_log(
                agent=str(result.get("agent") or "orchestrator"),
                action_type=str(action.get("type") or "unknown"),
                payload={"conversation_id": conversation_id, "action": action},
                status="pending",
            )

            if _is_agentic_action(action):
                agent_name = result.get("agent", "orchestrator")
                agent_obj = get_agent(agent_name) or orchestrator
                loop_result = await agent_obj._run_agentic_loop(
                    user_message=original_text,
                    conversation_id=conversation_id,
                    context=context,
                    initial_action=action,
                )
                results_text = "\n".join([
                    f"Étape {r['step']}: "
                    f"{str(r['result'].get('output', r['result'].get('message', '')))[:1000]}"
                    for r in loop_result.get("results", [])
                    if isinstance(r.get("step"), int)
                ])
                action_result = {
                    "ok": loop_result.get("final_status") != "failed",
                    "output": results_text,
                    "agentic": True,
                }
                if results_text:
                    fu = await orchestrator.handle(
                        (
                            f"Résultats :\n\n{results_text}\n\n"
                            f"Question : {original_text}\n\n"
                            "Synthétise."
                        ),
                        conversation_id=conversation_id,
                        voice_mode=voice_mode,
                    )
                    emotion = fu.get("emotion", emotion)
                    display_text = finalize_assistant_display_text(
                        fu.get("response", display_text)
                    )
                    final_meta = fu
            else:
                if _should_defer_action(display_text, action):
                    _maybe_store_pending_proposal(action, conversation_id)
                    action_result = {
                        "ok": True,
                        "deferred": True,
                        "message": display_text,
                    }
                else:
                    try:
                        action_result = await execute_action(action)
                        logger.info(
                            "[internal-action] %s → ok=%s",
                            action.get("type"),
                            action_result.get("ok") if action_result else None,
                        )
                        if action_result.get("needs_confirmation"):
                            _maybe_store_pending_proposal(action, conversation_id)
                    except Exception as e:
                        logger.exception("[internal-action] execute_action : %s", e)
                        action_result = {"ok": False, "message": str(e)}

                # 2e passe pour les actions avec followup
                if (
                    action_result
                    and not action_result.get("deferred")
                    and action.get("type") in ACTIONS_WITH_FOLLOWUP
                    and not action_result.get("needs_confirmation")
                    and action_result.get("ok")
                ):
                    try:
                        payload = _format_action_result_for_followup(action, action_result)
                        fu = await orchestrator.handle(
                            (
                                f"Résultat brut de l'action :\n\n{payload}\n\n"
                                f"Question originale : {original_text}\n\n"
                                "Résume ce résultat de façon claire et utile. Pas de bloc action."
                            ),
                            conversation_id=conversation_id,
                            voice_mode=voice_mode,
                        )
                        emotion = fu.get("emotion", emotion)
                        display_text = finalize_assistant_display_text(fu.get("response", display_text))
                        final_meta = fu
                    except Exception as e:
                        logger.exception("[internal-followup] %s", e)

        # Nettoyage final
        display_text = re.sub(r'```(?:json|action|save)\s*\{[\s\S]*?\}\s*```', '', display_text).strip()
        display_text = re.sub(r'^\s*\[\w+\]\s*\n?', '', display_text).strip()
        if not display_text:
            display_text = "Bien noté."

        try:
            save_message(
                conversation_id, "assistant", display_text,
                agent=final_meta.get("agent"),
                model=final_meta.get("model"),
                tokens_in=final_meta.get("tokens_in", 0),
                tokens_out=final_meta.get("tokens_out", 0),
                cost=final_meta.get("cost", 0.0),
            )
        except Exception as e:
            logger.error("[internal] save assistant message : %s", e)

        try:
            update_conversation_activity(conversation_id)
        except Exception:
            pass

        asyncio.create_task(_maybe_title_conversation(conversation_id))

        return {
            "text": display_text,
            "emotion": emotion,
            "action": action,
            "action_result": action_result,
            "agent": final_meta.get("agent"),
            "model": final_meta.get("model"),
            "cost": float(final_meta.get("cost") or 0.0),
        }
    except Exception as e:
        logger.exception("[_process_message_internal] %s", e)
        return {
            "text": "Une erreur est survenue lors du traitement.",
            "emotion": "neutral",
            "action": None,
            "action_result": None,
            "agent": None,
            "model": None,
            "cost": 0.0,
        }



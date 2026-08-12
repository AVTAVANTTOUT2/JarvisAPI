"""Pipeline interne de traitement des messages sans transport WebSocket."""

from __future__ import annotations

import logging
import re
from typing import Any

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
from api.action_confirmations import (
    is_imperative_confirmation,
    peek_pending_proposal,
    unmatched_confirmation_reply,
)
from api.chat_context import (
    _build_enriched_context,
)
from api.conversation_titles import (
    _maybe_title_conversation,
    schedule_conversation_title,
)
from api.llm_logging import _schedule_llm_log
from database import save_message, update_conversation_activity

logger = logging.getLogger("jarvis")


def _mark_voice_trace(trace: Any | None, event_name: str, **fields: Any) -> None:
    """Instrumente un tour vocal sans coupler le moteur aux transports."""
    if trace is None:
        return
    try:
        from audio import voice_latency as vl

        trace.mark(getattr(vl, event_name), **fields)
    except Exception:
        logger.debug("[internal] trace vocale ignorée", exc_info=True)


async def _process_message_internal(
    text: str,
    conversation_id: int,
    voice_mode: bool = False,
    confirmation_session_id: str | None = None,
    *,
    persist_assistant: bool = True,
    trace: Any | None = None,
) -> dict:
    """Pipeline JARVIS sans WebSocket — pour les endpoints REST (journal, contacts, etc.).

    Applique le même enrichissement de contexte que _process_message, appelle l'orchestrateur,
    exécute les actions avec 2e passe si nécessaire, sauvegarde le message assistant.

    Retourne {text, emotion, action, action_result, agent, model, cost}.

    ``persist_assistant=False`` est réservé à l'adaptateur vocal natif : il
    permet à celui-ci d'écrire le couple user/assistant hors de la boucle
    asyncio, sans dupliquer le moteur de tour ni ajouter SQLite à la latence
    micro-vers-enceinte.
    """
    try:
        confirmation_session_id = confirmation_session_id or f"internal:{conversation_id}"
        empty_response_cause: str | None = None
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
                confirmation_session_id=confirmation_session_id,
            )

        # ── Routage cognitif : tâche technique → délégation Cursor ──
        try:
            from api.chat_cognitive import maybe_delegate_chat_to_cursor, route_chat_text, should_run_cursor_cognitive_path

            intent = route_chat_text(original_text, voice_mode=voice_mode)
            if should_run_cursor_cognitive_path(original_text, intent, conversation_id, confirmation_session_id):
                # NB : la persistance du message user appartient à l'appelant
                # REST (même contrat que le reste de _process_message_internal).
                delegated = await maybe_delegate_chat_to_cursor(
                    original_text,
                    conversation_id,
                    intent=intent,
                    interaction_mode="voice" if voice_mode else "chat",
                )
                if delegated and delegated.get("handled"):
                    return {
                        "text": delegated["text"],
                        "emotion": delegated.get("emotion", "neutral"),
                        "action": {"type": "cursor_delegate", "job_id": delegated.get("job_id")},
                        "action_result": {"ok": True, "job_id": delegated.get("job_id")},
                        "agent": "cognitive",
                        "model": "router",
                        "cost": 0.0,
                        "routing": delegated.get("routing"),
                    }
        except Exception as e:
            logger.debug("[_process_message_internal] routage cognitif : %s", e)

        # Confirmation « oui / vas-y » sur une action en attente (REST)
        pending_action = peek_pending_proposal(
            conversation_id=conversation_id,
            session_id=confirmation_session_id,
        )
        confirmed_action = _pop_pending_action_if_confirmed(
            original_text,
            conversation_id,
            confirmation_session_id,
        )
        if confirmed_action is not None:
            _mark_voice_trace(
                trace,
                "ACTION_STARTED",
                action_type=confirmed_action.get("type") or "?",
            )
            try:
                action_result = await execute_action(confirmed_action)
            except Exception as e:
                logger.exception("[internal-pending] execute_action : %s", e)
                action_result = {"ok": False, "message": str(e)}
            _mark_voice_trace(
                trace,
                "ACTION_COMPLETED",
                action_type=confirmed_action.get("type") or "?",
                ok=bool(action_result.get("ok")),
            )

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
                and confirmed_action.get("type") in ACTIONS_WITH_FOLLOWUP
            ):
                try:
                    payload = _format_action_result_for_followup(confirmed_action, action_result)
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

            if persist_assistant:
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

        if is_imperative_confirmation(original_text):
            reply = unmatched_confirmation_reply()
            if persist_assistant:
                try:
                    save_message(
                        conversation_id,
                        "assistant",
                        reply["text"],
                        agent="orchestrator",
                        tokens_in=0,
                        tokens_out=0,
                        cost=0.0,
                    )
                except Exception as e:
                    logger.error("[internal-confirmation] save assistant : %s", e)
            try:
                update_conversation_activity(conversation_id)
            except Exception:
                pass
            return reply

        _mark_voice_trace(trace, "CONTEXT_BUILD_STARTED")
        context = await _build_enriched_context(text, conversation_id)
        _mark_voice_trace(trace, "CONTEXT_BUILD_COMPLETED")

        if voice_mode:
            context["voice_mode"] = True
        # Le pipeline enregistre le message assistant final (après actions).
        context["__defer_persist"] = True

        if "documents_context" in context:
            text = context.pop("documents_context") + "\n\n" + text

        llm_model = config.DEEPSEEK_FAST_MODEL if voice_mode else None
        _mark_voice_trace(trace, "LLM_QUEUE_ENTERED", model=llm_model)
        _mark_voice_trace(trace, "LLM_REQUEST_STARTED", model=llm_model, pass_index=1)
        result = await orchestrator.handle(
            text, conversation_id=conversation_id, voice_mode=voice_mode, context=context
        )
        _mark_voice_trace(
            trace,
            "LLM_COMPLETED",
            model=result.get("model") or llm_model,
            pass_index=1,
            text_chars=len(result.get("response") or ""),
        )
        full_response = result.get("response", "")
        emotion_raw, _ = extract_leading_emotion(full_response)
        emotion = emotion_raw or result.get("emotion", "neutral")

        action, after_action = _extract_action_from_text(full_response)
        display_text = finalize_assistant_display_text(after_action)

        action_result: dict | None = None
        final_meta = result
        action_for_client = action

        if action:
            _mark_voice_trace(
                trace,
                "ACTION_STARTED",
                action_type=action.get("type") or "?",
            )
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
                    safe_results = _format_action_result_for_followup(
                        {"type": "terminal"},
                        action_result,
                    )
                    fu = await orchestrator.handle(
                        (
                            f"Résultats :\n\n{safe_results}\n\n"
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
                    action_for_client = _maybe_store_pending_proposal(
                        action,
                        conversation_id,
                        confirmation_session_id,
                    )
                    action_result = {
                        "ok": True,
                        "deferred": True,
                        "needs_confirmation": True,
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
                            action_for_client = _maybe_store_pending_proposal(
                                action,
                                conversation_id,
                                confirmation_session_id,
                            )
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

            _mark_voice_trace(
                trace,
                "ACTION_COMPLETED",
                action_type=action.get("type") or "?",
                ok=bool(action_result and action_result.get("ok")),
            )

        # Nettoyage final
        raw_display_text = str(display_text or "")
        display_text = re.sub(r'```(?:json|action|save)\s*\{[\s\S]*?\}\s*```', '', display_text).strip()
        display_text = re.sub(r'^\s*\[\w+\]\s*\n?', '', display_text).strip()
        if not display_text:
            # Reponse vide : dire ce qui s'est passe, pas autre chose.
            #
            # « Bien noté. » prétend avoir compris et enregistré quelque chose
            # alors que le moteur n'a rien produit. C'est le même défaut que
            # « Je n'ai pas compris » côté vocal, en pire : l'un accuse à tort
            # la compréhension de l'utilisateur, l'autre lui fait croire que sa
            # demande est prise en compte. Les deux envoyaient chercher la cause
            # au mauvais endroit, pendant qu'elle restait invisible.
            #
            # Deux causes distinctes produisent ce vide, et les confondre
            # empêche de diagnostiquer :
            #   - le modele ne renvoie aucun contenu (reseau, quota, coupure) ;
            #   - il ne renvoie *que* le tag [emotion], que le parseur retire.
            tokens_out = int(final_meta.get("tokens_out") or 0)
            max_tokens = int(final_meta.get("max_tokens") or 0)
            reasoning_tokens = int(final_meta.get("reasoning_tokens") or 0)
            stop_reason = str(final_meta.get("stop_reason") or "")
            budget_exhausted = (
                not raw_display_text.strip()
                and (
                    stop_reason == "length"
                    or (max_tokens > 0 and tokens_out >= max_tokens)
                    or (max_tokens > 0 and reasoning_tokens >= max_tokens)
                )
            )
            if budget_exhausted:
                cause = "budget_epuise_avant_reponse"
            else:
                cause = "aucun_contenu" if not raw_display_text.strip() else "tag_emotion_seul"
            logger.warning(
                "[internal] Reponse vide (%s) — agent=%s tokens_out=%d "
                "reasoning_tokens=%d max_tokens=%d stop_reason=%s raw_chars=%d : "
                "le modele n'a rien produit, la demande de l'utilisateur n'est pas en cause",
                cause,
                final_meta.get("agent"),
                tokens_out,
                reasoning_tokens,
                max_tokens,
                stop_reason or "?",
                len(raw_display_text.strip()),
            )
            empty_response_cause = cause
            display_text = "Je n'ai pas obtenu de réponse."

        if persist_assistant:
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

        schedule_conversation_title(conversation_id, title_factory=_maybe_title_conversation)

        return {
            "text": display_text,
            "emotion": emotion,
            "action": action_for_client,
            "action_result": action_result,
            "agent": final_meta.get("agent"),
            "model": final_meta.get("model"),
            "cost": float(final_meta.get("cost") or 0.0),
            # Remonté jusqu'aux traces vocales : sans lui, un tour muet est
            # indiscernable d'un tour normal dans le journal de debug.
            "empty_response_cause": empty_response_cause,
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

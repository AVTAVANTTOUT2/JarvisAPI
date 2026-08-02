"""Pipeline vocal rapide optimisé pour les interactions mains libres."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import config
import llm
from actions import execute_action
from api.chat_actions import _format_action_result_for_followup, _is_agentic_action
from api.voice_prompts import build_action_followup_prompt, build_voice_system_prompt
from api.voice_fastpath import (
    _mark,
    _match_voice_control,
    _persist_voice_messages_async,
    _voice_llm_call,
    match_trivial_hail,
)
from api.voice_support import (
    _broadcast_voice_debug,
    _build_voice_confirmation_response,
    _fallback_action_response,
    _maybe_execute_pending_voice_action,
)
from app.fitness.voice import maybe_handle_fitness_voice
from database import _save_voice_debug_trace, get_conversation_history, get_current_screen_context
from jarvis.security.llm_data_boundary import sanitize_history_messages

logger = logging.getLogger("jarvis")

async def _process_voice_fast(
    text: str,
    conversation_id: int,
    *,
    stt_ms: int = 0,
    confirmation_session_id: str | None = None,
    trace: Any | None = None,
) -> dict:
    """Pipeline vocal ultra-rapide — routage cognitif + Flash + actions/Cursor."""
    import time as _time
    from api.voice_cognitive import maybe_handle_cognitive_voice

    _t0 = _time.time()
    confirmation_session_id = confirmation_session_id or f"local-voice:{conversation_id}"

    pending_result = await _maybe_execute_pending_voice_action(
        text,
        conversation_id,
        started_at=_t0,
        confirmation_session_id=confirmation_session_id,
    )
    if pending_result is not None:
        return pending_result

    # ── Contrôle barge-in déterministe (« arrête », « annule »…) ──
    control = _match_voice_control(text)
    if control is not None:
        _persist_voice_messages_async(conversation_id, text, control, 0.0, trace)
        return {
            "text": control,
            "emotion": "neutral",
            "cost": 0.0,
            "action": None,
            "latency_ms": round((_time.time() - _t0) * 1000),
            "debug_trace": {"input_text": text, "response_clean": control, "model": "control"},
        }

    # ── Interpellation triviale (« Jarvis ? », « tu m'entends ? ») ──
    hail = match_trivial_hail(text)
    if hail is not None:
        _persist_voice_messages_async(conversation_id, text, hail, 0.0, trace)
        latency_ms = round((_time.time() - _t0) * 1000)
        logger.info("[voice_fast] %dms (hail) — aucun appel LLM", latency_ms)
        return {
            "text": hail,
            "emotion": "neutral",
            "cost": 0.0,
            "action": None,
            "latency_ms": latency_ms,
            "debug_trace": {
                "input_text": text,
                "response_clean": hail,
                "model": "hail",
                "latency_stt_ms": int(stt_ms or 0),
                "latency_total_ms": latency_ms,
            },
        }

    # Match fitness étroit ; ``None`` laisse le pipeline existant continuer.
    if (fitness := maybe_handle_fitness_voice(text, conversation_id, stt_ms=stt_ms)) is not None:
        return fitness
    early = await maybe_handle_cognitive_voice(text, conversation_id, t0=_t0, stt_ms=stt_ms)
    if early and not early.get("__continue__"):
        return early
    debug_trace = (early or {}).get("debug_trace") or {}
    intent = (early or {}).get("intent")

    from agents import _get_horodatage
    horodatage = _get_horodatage()

    # Historique et contexte écran sont deux lectures SQLite. Menées en
    # séquence dans la coroutine, elles s'ajoutent telles quelles à la latence ;
    # menées de front dans des threads, elles coûtent le maximum des deux.
    _mark(trace, "CONTEXT_BUILD_STARTED")

    def _load_history() -> list[dict[str, str]]:
        raw = get_conversation_history(conversation_id, limit=10)
        raw_history = [
            {"role": m["role"], "content": m["content"]}
            for m in raw
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        if raw_history and raw_history[-1]["role"] == "user":
            raw_history = raw_history[:-1]
        return sanitize_history_messages(raw_history, max_messages=10)

    def _load_screen_context() -> str:
        ctx = get_current_screen_context()
        if not ctx or not ctx.get("app"):
            return ""
        out = f"\nECRAN : {ctx['app']}"
        if ctx.get("activity"):
            out += f" — {ctx['activity']}"
        if ctx.get("mood"):
            out += f" (mood: {ctx['mood']})"
        return out

    history_task = asyncio.create_task(asyncio.to_thread(_load_history))
    screen_task = asyncio.create_task(asyncio.to_thread(_load_screen_context))
    gathered = await asyncio.gather(history_task, screen_task, return_exceptions=True)

    history: list[dict[str, str]] = []
    if isinstance(gathered[0], BaseException):
        logger.debug("[voice_fast] get_conversation_history : %s", gathered[0])
    else:
        history = gathered[0]

    screen_context = "" if isinstance(gathered[1], BaseException) else gathered[1]
    _mark(trace, "CONTEXT_BUILD_COMPLETED", text_chars=len(screen_context))

    weather_city = getattr(config, "WEATHER_CITY", "Lille")

    system = build_voice_system_prompt(
        horodatage=horodatage,
        weather_city=weather_city,
        screen_context=screen_context,
    )


    # ── Capture debug ─────────────────────────────────────────────────────────
    if not debug_trace:
        from api.voice_cognitive import build_voice_debug_trace
        from jarvis.cognitive import route_request
        intent = intent or route_request(text, interaction_mode="voice")
        debug_trace = build_voice_debug_trace(text, intent, 0)
    debug_trace["latency_stt_ms"] = int(stt_ms or 0)
    debug_trace["system_prompt"] = system
    debug_trace["messages_sent"] = [{"role": m["role"], "content": m["content"][:200]} for m in history]
    debug_trace["model"] = getattr(config, "DEEPSEEK_FAST_MODEL", "deepseek-chat")

    # ── 4. Pass 1 : DeepSeek flash decide (reponse directe OU action seule) ────
    messages = history + [{"role": "user", "content": text}]
    total_cost: float = 0.0
    _mark(trace, "LLM_QUEUE_ENTERED", model=config.DEEPSEEK_FAST_MODEL)

    _t_llm1 = _time.time()
    _mark(trace, "LLM_REQUEST_STARTED", model=config.DEEPSEEK_FAST_MODEL)
    try:
        result = await _voice_llm_call(
            messages=messages,
            system=system,
            max_tokens=250,
            temperature=0.5,
            trace=trace,
        )
        debug_trace["latency_llm_pass1_ms"] = round((_time.time() - _t_llm1) * 1000)
        _mark(trace, "LLM_COMPLETED", model=config.DEEPSEEK_FAST_MODEL,
              text_chars=len(result.get("content") or ""))
        raw_response = result.get("content", "") or ""
        debug_trace["raw_response"] = raw_response
        debug_trace["tokens_in"] = int(result.get("tokens_in", 0))
        debug_trace["tokens_out"] = int(result.get("tokens_out", 0))
        debug_trace["cost"] = float(result.get("cost", 0.0))
        total_cost += float(result.get("cost", 0.0))
    except Exception as e:
        logger.error("[voice_fast] LLM erreur pass 1 : %s", e)
        debug_trace["error"] = str(e)
        debug_trace["latency_llm_pass1_ms"] = round((_time.time() - _t_llm1) * 1000)
        debug_trace["latency_total_ms"] = round((_time.time() - _t0) * 1000)
        asyncio.create_task(_broadcast_voice_debug(debug_trace))
        trace_id = _save_voice_debug_trace(debug_trace)
        return {
            "text": "Desole Monsieur, un probleme technique.",
            "emotion": "concerned",
            "cost": 0.0,
            "action": None,
            "latency_ms": debug_trace["latency_total_ms"],
            "debug_trace": debug_trace,
            "trace_id": trace_id,
        }

    # ── 5. Extraire l'emotion (tag [emotion] en debut de reponse) ─────────────
    emotion = "neutral"
    emotion_match = re.match(r'^\s*\[(\w+)\]\s*\n?', raw_response)
    if emotion_match:
        emotion = emotion_match.group(1)
        raw_response = raw_response[emotion_match.end():]

    debug_trace["emotion"] = emotion

    # ── 6. Detecter un bloc action ────────────────────────────────────────────
    action_match = re.search(r'```action\s*\n?(.*?)```', raw_response, re.DOTALL | re.IGNORECASE)

    if not action_match:
        # ── Pas d'action -> reponse directe (1 seul appel LLM) ─────────────────
        response_text = raw_response.strip()
        response_text = re.sub(r'```\w*\s*```', '', response_text).strip()
        debug_trace["response_clean"] = response_text
        debug_trace["latency_total_ms"] = round((_time.time() - _t0) * 1000)

        # ── Fallback reponse vide : DeepSeek peut ne rien produire sur des
        # transcriptions courtes/ambigues ("Oui ou non ?", bruit). On evite
        # le silence vocal en injectant une reponse minimale.
        if not response_text:
            response_text = "Je n'ai pas compris, Monsieur."
            emotion = "concerned"
            logger.debug("[voice_fast] Reponse LLM vide — fallback injecte")

        _persist_voice_messages_async(conversation_id, text, response_text, total_cost, trace)
        asyncio.create_task(_broadcast_voice_debug(debug_trace))
        trace_id = _save_voice_debug_trace(debug_trace)

        latency_ms = debug_trace["latency_total_ms"]
        logger.info(
            "[voice_fast] %.0fms (direct) — «%s» → «%s»",
            latency_ms, text[:40], response_text[:60],
        )
        return {
            "text": response_text,
            "emotion": emotion,
            "cost": total_cost,
            "action": None,
            "latency_ms": latency_ms,
            "debug_trace": debug_trace,
            "trace_id": trace_id,
        }

    # ── 7. Action detectee -> parser de maniere robuste ──────────────────────
    action_result: dict | None = None
    action: dict = {}
    try:
        if action_match:
            action = json.loads(action_match.group(1).strip())
            debug_trace["action_detected"] = action

            action_type_direct = action.get("type", "").strip()

            if _is_agentic_action(action):
                from agents import get_agent as _get_agent
                agent_obj = _get_agent("devops") or _get_agent("info")
                if agent_obj:
                    loop_result = await agent_obj._run_agentic_loop(
                        user_message=text,
                        conversation_id=conversation_id,
                        context=None,
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
                else:
                    action_result = await execute_action(action)

            # ── Handlers directs bypass execute_action (latence zero) ────
            elif action_type_direct == "search":
                query = (action.get("query") or "").strip()
                if not query:
                    action_result = {"ok": True, "message": "Aucun terme de recherche fourni."}
                else:
                    try:
                        from integrations.web_search import web_search
                        summary = await web_search(query)
                        action_result = {"ok": True, "message": summary[:600], "query": query}
                    except Exception as e:
                        action_result = {"ok": False, "message": f"Recherche indisponible : {e}"}

            elif action_type_direct == "sleep":
                try:
                    from scripts.audio_daemon import audio_daemon
                    audio_daemon.enter_sleep_mode()
                    action_result = {"ok": True, "message": "Mode veille active — micro en sourdine"}
                except Exception as e:
                    action_result = {"ok": False, "message": f"Veille indisponible : {e}"}

            elif action_type_direct == "wake":
                try:
                    from scripts.audio_daemon import audio_daemon
                    audio_daemon.exit_sleep_mode()
                    action_result = {"ok": True, "message": "Mode ecoute reactive"}
                except Exception as e:
                    action_result = {"ok": False, "message": f"Reveil indisponible : {e}"}

            else:
                action_result = await execute_action(action)

            try:
                from jarvis.event_bus import JarvisEvent, event_bus as _eb
                _action_type = action.get("type", "?")
                _action_params = {k: v for k, v in action.items() if k != "type"}
                asyncio.create_task(_eb.emit(JarvisEvent(
                    type="agent.action",
                    agent="voice",
                    data={"action_type": _action_type, "action_params": _action_params},
                )))
            except Exception:
                pass

            debug_trace["action_result"] = action_result

            try:
                from jarvis.event_bus import JarvisEvent, event_bus as _eb
                _action_type = action.get("type", "?")
                _result_str = "succès" if action_result.get("ok") else "échec"
                asyncio.create_task(_eb.emit(JarvisEvent(
                    type="agent.action_result",
                    agent="voice",
                    data={
                        "action_type": _action_type,
                        "result": _result_str,
                        "latency_ms": int((_time.time() - _t_llm1) * 1000),
                    },
                )))
            except Exception:
                pass
    except json.JSONDecodeError as e:
        logger.warning("[voice_fast] JSON action invalide : %s", e)
        action_result = {"ok": False, "error": "JSON invalide"}
    except Exception as e:
        logger.warning("[voice_fast] Action erreur : %s", e)
        action_result = {"ok": False, "error": str(e)}

    if action_result is None:
        action_result = {"ok": False, "error": "Aucun resultat"}

    if action_result.get("needs_confirmation"):
        return await _build_voice_confirmation_response(
            action=action,
            action_result=action_result,
            conversation_id=conversation_id,
            user_text=text,
            cost=total_cost,
            debug_trace=debug_trace,
            started_at=_t0,
            confirmation_session_id=confirmation_session_id,
        )

    # ── 8. Pass 2 : DeepSeek reformule le resultat de l'action ─────────────────
    action_type = action.get("type", "?")
    pass2_system = build_action_followup_prompt(horodatage)

    debug_trace["pass2_prompt"] = pass2_system

    _t_llm2 = _time.time()
    public_action_result = action_result
    if action_type == "clipboard":
        # Le contenu peut être affiché localement, mais ne repart jamais au cloud.
        response_text = _fallback_action_response(action_type, action_result)
        debug_trace["latency_llm_pass2_ms"] = 0
        debug_trace["pass2_skipped"] = "clipboard_local_only"
        debug_trace["action_result"] = "[LOCAL_ONLY]"
        public_action_result = {"ok": bool(action_result.get("ok"))}
    else:
        result_summary = _format_action_result_for_followup(action, action_result)
        pass2_messages = history + [
            {"role": "user", "content": text},
            {"role": "assistant", "content": f"[Action executee : {action_type}]"},
            {
                "role": "user",
                "content": (
                    f"Resultat de l'action {action_type} :\n{result_summary}\n\n"
                    "Formule une reponse vocale naturelle et concise (1-3 phrases) a "
                    "partir de ce resultat. Ne mentionne pas l'action elle-meme. "
                    "Donne l'information directement."
                ),
            },
        ]
        try:
            result2 = await llm.chat(
                messages=pass2_messages,
                model=config.DEEPSEEK_FAST_MODEL,
                system=pass2_system,
                max_tokens=min(getattr(config, "VOICE_MAX_TOKENS", 500), 300),
                temperature=0.7,
            )
            debug_trace["latency_llm_pass2_ms"] = round((_time.time() - _t_llm2) * 1000)
            response_text = result2.get("content", "") or ""
            debug_trace["pass2_response"] = response_text
            total_cost += float(result2.get("cost", 0.0))
            debug_trace["cost"] = total_cost
            debug_trace["tokens_in"] += int(result2.get("tokens_in", 0))
            debug_trace["tokens_out"] += int(result2.get("tokens_out", 0))

            # Extraire emotion pass 2
            em2 = re.match(r'^\s*\[(\w+)\]\s*\n?', response_text)
            if em2:
                emotion = em2.group(1)
                response_text = response_text[em2.end():]

            debug_trace["emotion"] = emotion
            response_text = response_text.strip()

            # Fallback si le LLM pass 2 a genere une reponse vide
            if not response_text:
                response_text = _fallback_action_response(action_type, action_result)

        except Exception as e:
            logger.error("[voice_fast] LLM erreur pass 2 : %s", e)
            debug_trace["latency_llm_pass2_ms"] = round((_time.time() - _t_llm2) * 1000)
            debug_trace["error"] = str(e)
            response_text = _fallback_action_response(action_type, action_result)

    # ── 9. Sauvegarder et retourner ────────────────────────────────────────────
    debug_trace["response_clean"] = response_text
    debug_trace["latency_total_ms"] = round((_time.time() - _t0) * 1000)

    _persist_voice_messages_async(conversation_id, text, response_text, total_cost, trace)
    asyncio.create_task(_broadcast_voice_debug(debug_trace))
    trace_id = _save_voice_debug_trace(debug_trace)

    latency_ms = debug_trace["latency_total_ms"]
    logger.info(
        "[voice_fast] %.0fms (action:%s) — «%s» → «%s»",
        latency_ms, action_type, text[:40], response_text[:60],
    )

    return {
        "text": response_text,
        "emotion": emotion,
        "cost": total_cost,
        "action": public_action_result,
        "latency_ms": latency_ms,
        "debug_trace": debug_trace,
        "trace_id": trace_id,
    }

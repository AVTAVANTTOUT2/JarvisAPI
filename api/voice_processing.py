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
from api.chat_actions import _is_agentic_action
from api.voice_support import _broadcast_voice_debug, _fallback_action_response, _save_voice_messages
from database import _save_voice_debug_trace, get_conversation_history, get_current_screen_context

logger = logging.getLogger("jarvis")

# ── Commandes de contrôle vocal (barge-in) — zéro LLM, réponse instantanée ──
#
# Politique produit (Option A — commande uniquement) :
# - Pendant la lecture TTS, seuls les énoncés courts (≤30 car.) correspondant
#   exactement à une commande de contrôle interrompent la synthèse.
# - Exemples reconnus : « arrête », « stop », « annule », « silence », « continue ».
# - Toute autre parole pendant le TTS est ignorée (pas de barge-in libre).
# - Hors TTS, les mêmes commandes sont traitées en priorité avant le LLM.
# - Annulation explicite côté client : message WebSocket ``voice_cancel``.
_VOICE_CONTROL_MAX_LEN = 30
_VOICE_CONTROL_COMMANDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("arrete", "arrête", "stop", "tais-toi", "tais toi", "chut", "silence", "stoppe",
      "plus court", "coupe"),
     "Bien, Monsieur."),
    (("annule", "annule tout", "laisse tomber", "oublie", "oublie ca", "oublie ça"),
     "C'est annulé, Monsieur."),
    (("continue", "poursuis", "vas-y continue"),
     "Je continue, Monsieur."),
    (("merci ca suffit", "merci ça suffit", "c'est tout", "c'est bon merci", "ca suffit", "ça suffit"),
     "À votre service, Monsieur."),
)


def _match_voice_control(text: str) -> str | None:
    """Commande de contrôle barge-in ? Retourne la réponse fixe ou None."""
    t = (text or "").strip().lower().rstrip(".!?, ")
    if not t or len(t) > _VOICE_CONTROL_MAX_LEN:
        return None
    for keywords, response in _VOICE_CONTROL_COMMANDS:
        if t in keywords:
            return response
    return None


async def _process_voice_fast(text: str, conversation_id: int, *, stt_ms: int = 0) -> dict:
    """Pipeline vocal ultra-rapide — routage cognitif + Flash + actions/Cursor."""
    import time as _time
    from api.voice_cognitive import maybe_handle_cognitive_voice

    _t0 = _time.time()

    # ── Contrôle barge-in déterministe (« arrête », « annule »…) ──
    control = _match_voice_control(text)
    if control is not None:
        _save_voice_messages(conversation_id, text, control, 0.0)
        return {
            "text": control,
            "emotion": "neutral",
            "cost": 0.0,
            "action": None,
            "latency_ms": round((_time.time() - _t0) * 1000),
            "debug_trace": {"input_text": text, "response_clean": control, "model": "control"},
        }

    early = await maybe_handle_cognitive_voice(text, conversation_id, t0=_t0, stt_ms=stt_ms)
    if early and not early.get("__continue__"):
        return early
    debug_trace = (early or {}).get("debug_trace") or {}
    intent = (early or {}).get("intent")

    # ── 0. Persona condensee pour le vocal (~50 tokens) ────────────────────────
    VOICE_PERSONA = (
        "Tu es JARVIS, majordome IA d'{}. Ton britannique, concis, sec. "
        "Tu l'appelles 'Monsieur' avec ironie bienveillante. "
        "Jamais d'emoji. Jamais de presentation ('je suis JARVIS'). "
        "Jamais de 'je reviens vers vous' ou 'un instant'. "
        "3 phrases max a l'oral. Pas de Markdown."
    ).format(config.USER_NAME)

    # ── 1. Contexte temporel minimal ──────────────────────────────────────────
    from agents import _get_horodatage
    horodatage = _get_horodatage()

    # ── 2. Historique recent (10 derniers messages, pas de build_full_context) ──
    history: list[dict[str, str]] = []
    try:
        raw = get_conversation_history(conversation_id, limit=10)
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in raw
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        if history and history[-1]["role"] == "user":
            history = history[:-1]
    except Exception as e:
        logger.debug("[voice_fast] get_conversation_history : %s", e)

    # ── Contexte ecran ──────────────────────────────────────────────────
    screen_context = ""
    try:
        ctx = get_current_screen_context()
        if ctx and ctx.get("app"):
            screen_context = f"\nECRAN : {ctx['app']}"
            if ctx.get("activity"):
                screen_context += f" — {ctx['activity']}"
            if ctx.get("mood"):
                screen_context += f" (mood: {ctx['mood']})"
    except Exception:
        pass

    # ── 3. System prompt compact — permet de répondre ET d'agir ──
    weather_city = getattr(config, "WEATHER_CITY", "Lille")

    ACTIONS_COMPACT = """ACTIONS (bloc ```action {"type":"...", ...} ``` — tu peux répondre ET agir) :
weather(city) | open_app(app_name) | task(title,priority) | reminder(title,due_date)
calendar(range?) | calendar_create(summary,start,end?) | mood(score)
mail(to,subject,body) | mail_read | note(content) | find_file(query)
clipboard(action,text?) | system_info(info) | name_place(name) | where_am_i | day_route
search_conversations(query) | search(query) | sleep | wake
tv(command) — commandes TV : on, off, home, back, vol_up, vol_down, mute, next, prev, play, pause
terminal(command) — COMMANDE SHELL uniquement (ls, grep, python...), JAMAIS une question

RÈGLES :
- Questions d'actu, sport, résultats, infos : search(query) — pas la météo ni l'heure
- Météo : weather(city) — pas search
- Heure, date, aujourd'hui : réponds directement avec l'horodatage fourni
- Recherche dans tes conversations passées : search_conversations(query)
- Commande système : terminal(command) — le command doit être un shell valide
- Tâches complexes (code, analyse, debug) : terminal(command, complex:true)
- "mets-toi en veille" / "dors" / "pause" : sleep
- "réveille-toi" / "je suis là" : wake
- TV : si l'utilisateur parle d'allumer, éteindre, ou contrôler la télévision → tv(command)
- Si le contexte mémoire contient déjà l'info (météo chargée, calendar...) : réponds directement
- Tu peux répondre ET inclure un bloc action dans la même réponse.
- Pour les questions simples (heure, date, fait) : réponds directement.
- Pour les actions : ajoute le bloc action après ta réponse, ou uniquement le bloc action si c'est purement exécutif.
- Si l'utilisateur dit "oui" ou "vas-y" après ta proposition : produis immédiatement le bloc action."""

    system = f"""{horodatage}
{VOICE_PERSONA}
LIEU : {weather_city}, France{screen_context}

{ACTIONS_COMPACT}

RÈGLES SUPPLEMENTAIRES :
- Aucun bloc action = pas autorise a en inventer. Utilise uniquement les types decrits ci-dessus."""


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

    _t_llm1 = _time.time()
    try:
        result = await llm.chat(
            messages=messages,
            model=config.DEEPSEEK_FAST_MODEL,
            system=system,
            max_tokens=250,
            temperature=0.5,
        )
        debug_trace["latency_llm_pass1_ms"] = round((_time.time() - _t_llm1) * 1000)
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
        # Fallback : JSON brut inline avec "type"
        action_match = re.search(r'\{\s*"type"\s*:\s*"(\w+)"\s*[,}].*?\}', raw_response, re.DOTALL)

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

        _save_voice_messages(conversation_id, text, response_text, total_cost)
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
            json_str = action_match.group(0)
            # Si c'est un match inline (pas de backticks), extraire l'objet JSON complet
            if not json_str.startswith("```"):
                # Trouver les bornes de l'objet JSON
                start = action_match.start()
                depth = 0
                end = start
                for i, ch in enumerate(raw_response[start:], start):
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                json_str = raw_response[start:end]
            else:
                # Format ```action ...``` → prendre le contenu
                inner = re.search(r'```action\s*\n?(.*?)```', json_str, re.DOTALL | re.IGNORECASE)
                if inner:
                    json_str = inner.group(1).strip()
                else:
                    json_str = action_match.group(1).strip()

            action = json.loads(json_str)
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

            # ── Event bus : action detectee ──
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

            # ── Event bus : resultat action ──
            try:
                from jarvis.event_bus import JarvisEvent, event_bus as _eb
                _action_type = action.get("type", "?")
                _result_str = str(action_result.get("output", action_result.get("message", action_result)))[:300]
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

    # ── 8. Pass 2 : DeepSeek reformule le resultat de l'action ─────────────────
    action_type = action.get("type", "?")
    result_summary = json.dumps(action_result, ensure_ascii=False, default=str)[:800]

    pass2_messages = history + [
        {"role": "user", "content": text},
        {"role": "assistant", "content": f"[Action executee : {action_type}]"},
        {
            "role": "user",
            "content": (
                f"Resultat de l'action {action_type} : {result_summary}\n\n"
                "Formule une reponse vocale naturelle et concise (1-3 phrases) a "
                "partir de ce resultat. Ne mentionne pas l'action elle-meme. "
                "Donne l'information directement."
            ),
        },
    ]

    pass2_system = f"""Tu es JARVIS, assistant personnel de {config.USER_NAME}. Tu parles a l'ORAL.
Formule une reponse naturelle a partir du resultat d'action ci-dessous.
1 a 3 phrases max. Pas de Markdown. Pas de "voici le resultat".
Donne l'information directement comme si tu la savais.
Date : {horodatage}."""

    debug_trace["pass2_prompt"] = pass2_system

    _t_llm2 = _time.time()
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

    _save_voice_messages(conversation_id, text, response_text, total_cost)
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
        "action": action_result,
        "latency_ms": latency_ms,
        "debug_trace": debug_trace,
        "trace_id": trace_id,
    }

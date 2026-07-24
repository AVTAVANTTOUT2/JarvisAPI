"""Persistance, diffusion de traces et réponses de repli vocales."""

from __future__ import annotations

import logging
from typing import Any

import config
from database import save_message, update_conversation_activity
from websocket_registry import broadcast_ws

logger = logging.getLogger("jarvis")


async def _maybe_execute_pending_voice_action(
    text: str,
    conversation_id: int,
    *,
    started_at: float,
) -> dict[str, Any] | None:
    """Consomme une confirmation vocale sans refaire appel au modèle."""
    import time

    from actions import execute_action
    from api.chat_actions import _pop_pending_action_if_confirmed

    pending_action = _pop_pending_action_if_confirmed(text, conversation_id)
    if pending_action is None:
        return None

    pending_result = await execute_action(pending_action)
    pending_type = str(pending_action.get("type") or "action")
    response_text = _fallback_action_response(pending_type, pending_result)
    _save_voice_messages(conversation_id, text, response_text, 0.0)
    return {
        "text": response_text,
        "emotion": "neutral",
        "cost": 0.0,
        "action": pending_result,
        "latency_ms": round((time.time() - started_at) * 1000),
        "debug_trace": {
            "input_text": text,
            "response_clean": response_text,
            "model": "pending_confirmation",
            "action_result": pending_result,
        },
    }


async def _build_voice_confirmation_response(
    *,
    action: dict[str, Any],
    action_result: dict[str, Any],
    conversation_id: int,
    user_text: str,
    cost: float,
    debug_trace: dict[str, Any],
    started_at: float,
) -> dict[str, Any]:
    """Persiste et expose un plan vocal sans lancer la seconde passe LLM."""
    import asyncio
    import time

    from api.chat_actions import _maybe_store_pending_proposal
    from database import _save_voice_debug_trace

    _maybe_store_pending_proposal(action, conversation_id)
    response_text = _fallback_action_response(
        str(action.get("type") or "terminal"),
        action_result,
    )
    debug_trace["response_clean"] = response_text
    debug_trace["latency_total_ms"] = round((time.time() - started_at) * 1000)
    _save_voice_messages(conversation_id, user_text, response_text, cost)
    asyncio.create_task(_broadcast_voice_debug(debug_trace))
    trace_id = _save_voice_debug_trace(debug_trace)
    return {
        "text": response_text,
        "emotion": "neutral",
        "cost": cost,
        "action": action_result,
        "latency_ms": debug_trace["latency_total_ms"],
        "debug_trace": debug_trace,
        "trace_id": trace_id,
    }


async def _broadcast_voice_debug(trace: dict[str, Any]) -> None:
    """Broadcast la trace de debug vocal via WebSocket (fire-and-forget)."""
    try:
        safe_trace = {
            k: v for k, v in trace.items()
            if isinstance(v, (str, int, float, bool, list, dict, type(None)))
        }
        await broadcast_ws({
            "type": "voice_debug_trace",
            **safe_trace,
        })
    except Exception as e:
        logger.debug("[voice_fast] broadcast debug: %s", e)


def _fallback_action_response(action_type: str, result: dict) -> str:
    """Reformulation basique si le LLM pass 2 echoue (pas d'appel API)."""
    if result.get("needs_confirmation"):
        commands = result.get("commands") or []
        count = len(commands) if isinstance(commands, list) else 0
        return (
            f"J'ai préparé {count} commande{'s' if count != 1 else ''} "
            "dans un espace isolé. Dites oui pour confirmer exactement ce plan."
        )
    if not result.get("ok"):
        return "Desole Monsieur, l'action a echoue."

    if action_type == "weather":
        data = result.get("data", {})
        city = data.get("city", config.WEATHER_CITY)
        temp = data.get("temp", "?")
        desc = data.get("description", "")
        return f"Il fait {temp} degres a {city}, {desc}."

    if action_type == "open_app":
        app_name = result.get("app_name", "l'application")
        return f"{app_name} est ouverte, Monsieur."

    if action_type == "task":
        return "Tache creee, Monsieur."

    if action_type == "reminder":
        return "Rappel cree, Monsieur."

    if action_type == "calendar":
        events = result.get("events", [])
        if not events:
            return "Votre agenda est vide, Monsieur."
        ev = events[0]
        return (
            f"Prochain evenement : {ev.get('summary', '?')} "
            f"a {ev.get('start', '?')}."
        )

    if action_type == "calendar_create":
        return "Evenement ajoute a votre agenda, Monsieur."

    if action_type == "terminal":
        output = result.get("output", "")[:100]
        return f"Commande executee. {output}" if output else "Commande executee, Monsieur."

    if action_type == "mood":
        return "Humeur enregistree, Monsieur."

    if action_type == "mail":
        return "Brouillon prepare, Monsieur."

    if action_type == "mail_read":
        emails = result.get("emails", [])
        count = len(emails) if emails else 0
        if count == 0:
            return "Vous n'avez aucun email non lu, Monsieur."

        stats = result.get("stats", {})
        urgent = stats.get("urgent", 0)
        response = f"Vous avez {count} email{'s' if count > 1 else ''} non lu{'s' if count > 1 else ''}"
        if urgent > 0:
            response += f" dont {urgent} urgent{'s' if urgent > 1 else ''}"
        response += "."

        # Ajouter les 3 premiers résumés
        summaries = []
        for e in emails[:3]:
            sender = e.get("from", "")
            s_name = sender.split("<")[0].strip() if "<" in sender else sender
            summary = (e.get("summary") or "").strip()
            if summary:
                summaries.append(f"{s_name} : {summary[:100]}")
        if summaries:
            response += " " + " | ".join(summaries)

        return response

    if action_type == "note":
        return "Note enregistree, Monsieur."

    if action_type == "find_file":
        files = result.get("files", [])
        count = len(files) if files else result.get("count", 0)
        if count == 0:
            return "Aucun fichier trouve, Monsieur."
        return f"{count} fichier(s) trouve(s), Monsieur."

    if action_type == "clipboard":
        if result.get("action") == "set" or "text" in result:
            return "Copie dans le presse-papiers, Monsieur."
        content = result.get("content", "")
        preview = content[:80] if content else ""
        return f"Presse-papiers : {preview}" if preview else "Presse-papiers vide, Monsieur."

    if action_type == "system_info":
        info_type = result.get("info", "")
        if "battery" in str(result) or info_type == "battery":
            pct = result.get("percentage", "?")
            return f"Batterie a {pct}%, Monsieur."
        if "wifi" in str(result) or info_type == "wifi":
            ssid = result.get("ssid", "inconnu")
            return f"Wi-Fi connecte a {ssid}, Monsieur."
        if "apps" in str(result) or info_type == "apps":
            apps = result.get("apps", [])
            return f"{len(apps)} applications ouvertes, Monsieur."
        # disk / fallback
        free = result.get("free", "?")
        return f"Espace disque disponible : {free}, Monsieur."

    if action_type == "name_place":
        name = result.get("name", result.get("message", "le lieu"))
        return f"Lieu nomme : {name}, Monsieur."

    if action_type == "where_am_i":
        msg = result.get("message", "Position inconnue.")
        return f"{msg}, Monsieur."

    if action_type == "day_route":
        msg = result.get("message", "Aucune visite aujourd'hui.")
        return f"{msg}, Monsieur."

    if action_type == "search_conversations":
        count = result.get("count", 0)
        if count == 0:
            return "Aucune conversation trouvee, Monsieur."
        return f"{count} conversation(s) trouvee(s), Monsieur."

    return "C'est fait, Monsieur."


def _save_voice_messages(
    conversation_id: int, user_text: str, assistant_text: str, cost: float
) -> None:
    """Sauvegarde les messages vocaux en DB (silencieux si erreur)."""
    try:
        save_message(conversation_id, "user", user_text)
        save_message(
            conversation_id,
            "assistant",
            assistant_text,
            agent="voice",
            model=config.DEEPSEEK_FAST_MODEL,
            cost=cost,
        )
        update_conversation_activity(conversation_id)
    except Exception as e:
        logger.debug("[voice_fast] save_message : %s", e)

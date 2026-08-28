"""Persistance, diffusion de traces et réponses de repli vocales."""

from __future__ import annotations

import logging
from typing import Any

import config
from database import save_message, update_conversation_activity
from websocket_registry import broadcast_ws

logger = logging.getLogger("jarvis")


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
        return "L'action a echoue."

    if action_type == "weather":
        data = result.get("data", {})
        city = data.get("city", config.WEATHER_CITY)
        temp = data.get("temp", "?")
        desc = data.get("description", "")
        return f"Il fait {temp} degres a {city}, {desc}."

    if action_type in {"open_app", "launch"}:
        app_name = result.get("app_name")
        target = result.get("target")
        if app_name and result.get("kind") == "app":
            return f"{app_name} est ouverte."
        if target:
            return f"C'est ouvert : {target}."
        return "C'est ouvert."

    if action_type == "task":
        return "Tache creee."

    if action_type == "reminder":
        return "Rappel cree."

    if action_type == "calendar":
        events = result.get("events", [])
        if not events:
            return "Votre agenda est vide."
        ev = events[0]
        return (
            f"Prochain evenement : {ev.get('summary', '?')} "
            f"a {ev.get('start', '?')}."
        )

    if action_type == "calendar_create":
        return "Evenement ajoute a votre agenda."

    if action_type == "terminal":
        output = result.get("output", "")[:100]
        return f"Commande executee. {output}" if output else "Commande executee."

    if action_type == "mood":
        return "Humeur enregistree."

    if action_type == "mail":
        return "Brouillon prepare."

    if action_type == "mail_read":
        emails = result.get("emails", [])
        count = len(emails) if emails else 0
        if count == 0:
            return "Vous n'avez aucun email non lu."

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
        return "Note enregistree."

    if action_type == "find_file":
        files = result.get("files", [])
        count = len(files) if files else result.get("count", 0)
        if count == 0:
            return "Aucun fichier trouve."
        return f"{count} fichier(s) trouve(s)."

    if action_type == "clipboard":
        if result.get("action") == "set" or "text" in result:
            return "Copie dans le presse-papiers."
        return (
            "Presse-papiers lu localement. "
            "Son contenu n'a pas été transmis."
        )

    if action_type == "system_info":
        info_type = result.get("info", "")
        if "battery" in str(result) or info_type == "battery":
            pct = result.get("percentage", "?")
            return f"Batterie a {pct}%."
        if "wifi" in str(result) or info_type == "wifi":
            ssid = result.get("ssid", "inconnu")
            return f"Wi-Fi connecte a {ssid}."
        if "apps" in str(result) or info_type == "apps":
            apps = result.get("apps", [])
            return f"{len(apps)} applications ouvertes."
        # disk / fallback
        free = result.get("free", "?")
        return f"Espace disque disponible : {free}."

    if action_type == "name_place":
        name = result.get("name", result.get("message", "le lieu"))
        return f"Lieu nomme : {name}."

    # Ces deux messages sont déjà des phrases complètes : leur ajouter un point
    # produisait « Position inconnue.. » une fois l'adresse retirée.
    if action_type == "where_am_i":
        return str(result.get("message") or "Position inconnue.")

    if action_type == "day_route":
        return str(result.get("message") or "Aucune visite aujourd'hui.")

    if action_type == "search_conversations":
        count = result.get("count", 0)
        if count == 0:
            return "Aucune conversation trouvee."
        return f"{count} conversation(s) trouvee(s)."

    return "C'est fait."


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

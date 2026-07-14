"""État, parsing et exécution des actions du chat."""

from __future__ import annotations

import asyncio
import logging
import re

from fastapi import WebSocket

import config
from actions import execute_action
from agents.autonomous_loop import run_autonomous_loop
from agents.display_text import finalize_assistant_display_text
from api.chat_context import _build_enriched_context, _maybe_title_conversation
from database import save_message, update_conversation_activity

logger = logging.getLogger("jarvis")


# ── WebSocket chat ──────────────────────────────────────────

# Mémoire de proposition en attente — quand JARVIS propose "Veux-tu que je fasse X ?"
# et que l'utilisateur répond "oui" / "vas-y", on exécute immédiatement.
_pending_proposal: dict | None = None

_ACTION_RE = re.compile(r"```action\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)

# Regex fallback pour JSON inline hors backticks
_ACTION_JSON_INLINE_RE = re.compile(
    r'\{\s*"type"\s*:\s*"(\w+)"\s*[,}].*?\}',
    re.DOTALL,
)

ACTIONS_WITH_FOLLOWUP = frozenset({
    "terminal",
    "find_file",
    "system_info",
    "clipboard",
    "search_conversations",
    "weather",
    "calendar",
    "calendar_create",
    "open_app",
    "mail_read",
    "name_place",
    "where_am_i",
    "day_route",
    "tv",
})

# Types d'actions qui peuvent déclencher la boucle agentique (multi-étapes)
AGENTIC_ACTION_TYPES = frozenset({"terminal"})


def _is_agentic_action(action: dict) -> bool:
    """Boucle agentique uniquement pour terminal + complex:true (pas un simple ls/grep)."""
    return (
        action.get("type") in AGENTIC_ACTION_TYPES
        and action.get("complex") is True
    )


async def _run_loop_mode_ws(
    ws: WebSocket,
    task: str,
    conversation_id: int,
    *,
    voice_mode: bool = False,
) -> dict:
    """Exécute le mode /loop autonome avec événements WebSocket temps réel."""
    context = await _build_enriched_context(task, conversation_id)
    if voice_mode:
        context["voice_mode"] = True

    async def _on_event(event_type: str, data: dict) -> None:
        await ws.send_json({"type": event_type, **data})

    await ws.send_json({
        "type": "status",
        "content": f"Mode autonome activé — {task[:120]}",
    })

    loop_result = await run_autonomous_loop(
        task,
        conversation_id,
        context,
        on_event=_on_event,
    )

    synthesis = loop_result.get("synthesis") or "Boucle terminée."
    emotion = "neutral"
    display_text = finalize_assistant_display_text(synthesis)

    try:
        save_message(
            conversation_id,
            "assistant",
            display_text,
            agent="loop",
            model=config.LOOP_MODEL,
            cost=float(loop_result.get("total_cost") or 0.0),
        )
        update_conversation_activity(conversation_id)
        asyncio.create_task(_maybe_title_conversation(conversation_id))
    except Exception as exc:
        logger.warning("[loop] save_message : %s", exc)

    await ws.send_json({
        "type": "response",
        "agent": "loop",
        "category": "LOOP",
        "content": display_text,
        "model": config.LOOP_MODEL,
        "cost": loop_result.get("total_cost", 0.0),
        "emotion": emotion,
        "loop": {
            "status": loop_result.get("final_status"),
            "steps": loop_result.get("step_count"),
            "llm_calls": loop_result.get("total_llm_calls"),
        },
    })

    return {"emotion": emotion, "response": display_text, "loop_result": loop_result}


async def _run_loop_mode_internal(
    task: str,
    conversation_id: int,
    *,
    voice_mode: bool = False,
) -> dict:
    """Mode /loop sans WebSocket (REST, daemon, iMessage)."""
    context = await _build_enriched_context(task, conversation_id)
    if voice_mode:
        context["voice_mode"] = True

    loop_result = await run_autonomous_loop(task, conversation_id, context)
    synthesis = loop_result.get("synthesis") or "Boucle terminée."
    display_text = finalize_assistant_display_text(synthesis)

    try:
        save_message(
            conversation_id,
            "assistant",
            display_text,
            agent="loop",
            model=config.LOOP_MODEL,
            cost=float(loop_result.get("total_cost") or 0.0),
        )
        update_conversation_activity(conversation_id)
    except Exception as exc:
        logger.warning("[loop] save_message internal : %s", exc)

    return {
        "text": display_text,
        "emotion": "neutral",
        "agent": "loop",
        "model": config.LOOP_MODEL,
        "cost": float(loop_result.get("total_cost") or 0.0),
        "loop_result": loop_result,
    }


_PROPOSAL_MARKERS = (
    "veux-tu", "veux tu", "voulez-vous", "souhaites-tu", "souhaites tu",
    "dois-je", "dois je", "puis-je", "puis je", "tu confirmes",
    "confirmer", "je peux le", "je peux la", "je peux les",
    "shall i", "want me to", "should i",
)


def _should_defer_action(display_text: str, action: dict) -> bool:
    """Reporte l'exécution si JARVIS pose une question de confirmation."""
    if action.get("type") == "mail" and not action.get("confirmed"):
        return False  # mail : brouillon immédiat, pending séparé
    text = (display_text or "").lower()
    if "?" not in text:
        return False
    return any(marker in text for marker in _PROPOSAL_MARKERS)


def _pop_pending_action_if_confirmed(text: str, conversation_id: int) -> dict | None:
    """Retire et retourne l'action pending si l'utilisateur confirme (« oui », « vas-y »…)."""
    global _pending_proposal

    if not _pending_proposal:
        return None

    if _pending_proposal.get("conversation_id") != conversation_id:
        _pending_proposal = None
        return None

    text_lower = text.strip().lower()
    confirmation_patterns = (
        "oui", "vas-y", "vas y", "fais-le", "fais le", "ok", "okay",
        "d'accord", "go", "lance", "exécute", "execute", "yes",
        "pourquoi pas", "je veux bien", "allez", "allé", "fonce",
        "oui vas-y", "oui vas y", "oui fais le", "oui stp", "oui merci",
    )

    is_confirmation = (
        text_lower in confirmation_patterns
        or any(text_lower.startswith(p) for p in confirmation_patterns if len(p) > 3)
    )

    if not is_confirmation:
        if _pending_proposal:
            logger.info("[pending] Proposition annulée (user a dit autre chose)")
        _pending_proposal = None
        return None

    action = {**_pending_proposal["action"], "confirmed": True}
    _pending_proposal = None
    logger.info(
        "[pending] Confirmation détectée « %s » → exécution de %s",
        text[:60], action.get("type"),
    )
    return action


def _maybe_store_pending_proposal(action: dict, conversation_id: int) -> None:
    """Stocke une proposition d'action en attente de confirmation de l'utilisateur.

    Quand JARVIS dit « Veux-tu que je fasse X ? » avec un bloc action,
    on mémorise l'action pour que si l'utilisateur répond « oui » / « vas-y »
    au message suivant, l'action soit exécutée immédiatement.
    """
    global _pending_proposal
    _pending_proposal = {
        "conversation_id": conversation_id,
        "action": action,
    }


async def _check_pending_proposal(
    ws, text: str, conversation_id: int,
) -> dict | None:
    """Vérifie si l'utilisateur confirme une proposition en attente.

    Retourne le résultat de l'action si confirmée, None sinon.
    """
    action = _pop_pending_action_if_confirmed(text, conversation_id)
    if action is None:
        return None

    await ws.send_json({
        "type": "status",
        "content": f"Exécution de l'action : {action.get('type')}…",
    })

    try:
        return await execute_action(action)
    except Exception as e:
        logger.exception("[pending] execute_action : %s", e)
        return {"ok": False, "message": str(e)}


def _format_action_result_for_followup(action: dict, action_result: dict) -> str:
    """Texte dense pour la 2e passe orchestrateur (réformulation)."""
    t = action.get("type", "")
    if t == "terminal":
        parts = [f"Instruction : {action.get('command', '')}"]
        if action_result.get("code"):
            for block in action_result["code"]:
                parts.append(f"Code {block.get('language', 'python')} :\n{str(block.get('code', ''))[:1000]}")
        if action_result.get("output"):
            parts.append("Résultat :\n" + str(action_result["output"])[:3000])
        if action_result.get("stdout"):
            parts.append("Sortie :\n" + str(action_result.get("stdout", "")))
        if action_result.get("stderr"):
            parts.append("Erreurs :\n" + str(action_result.get("stderr", "")))
        if action_result.get("errors"):
            parts.append("Erreurs :\n" + "\n".join(str(e) for e in action_result["errors"]))
        if action_result.get("error"):
            parts.append("Erreur : " + str(action_result["error"]))
        if action_result.get("summary"):
            parts.append("Résumé : " + str(action_result["summary"])[:500])
        return "\n\n".join(parts)
    if t == "find_file":
        files = action_result.get("files") or []
        if not files:
            return "Aucun fichier correspondant."
        return "Fichiers trouvés :\n" + "\n".join(files)
    if t == "clipboard":
        return "Contenu du presse-papier :\n" + str(action_result.get("content", ""))
    if t == "system_info":
        lines = [f"{k}: {v}" for k, v in action_result.items() if k != "ok"]
        return "\n".join(lines[:200])
    if t == "where_am_i":
        return action_result.get("message") or str(action_result.get("location") or "")
    if t == "day_route":
        return action_result.get("message") or ""
    if t == "weather":
        w = action_result.get("weather") or {}
        return (
            f"Météo {w.get('city', '?')} : {w.get('temp', '?')}°C, "
            f"{w.get('description', '?')}, humidité {w.get('humidity', '?')}%, "
            f"vent {w.get('wind_speed', '?')} km/h"
        )
    if t == "calendar":
        events = action_result.get("events") or []
        if not events:
            return "Aucun événement à l'agenda pour cette période."
        lines = [f"- {e.get('start', '?')} : {e.get('summary', e.get('title', '?'))}" for e in events[:20]]
        return "Événements :\n" + "\n".join(lines)
    if t == "calendar_create":
        return action_result.get("message") or "Événement créé."
    if t == "open_app":
        return action_result.get("message") or f"Application {action.get('name', '?')} ouverte."
    if t == "mail_read":
        emails = action_result.get("emails") or []
        if not emails:
            return "Aucun mail non lu."
        lines = [f"- De: {e.get('from', '?')} | {e.get('subject', '?')}" for e in emails[:10]]
        return "Mails non lus :\n" + "\n".join(lines)
    if t == "name_place":
        return action_result.get("message") or "Lieu enregistré."
    return str(action_result)[:8000]


def _extract_action_from_text(text: str) -> tuple[dict | None, str]:
    """Extrait un bloc ```action {JSON}``` d'une réponse — tolérant au format.

    Accepte :
    - `` ```action\\n{JSON}\\n``` `` (standard)
    - `` ```action {JSON}``` `` (sans nouvelle ligne)
    - JSON inline hors backticks (fallback)

    Retourne (action_dict, texte_propre) ou (None, text).
    """
    import json as _json

    # 1. Format standard / tolérant
    m = _ACTION_RE.search(text)
    if m:
        json_str = m.group(1).strip()
        clean = (text[: m.start()] + text[m.end():]).strip()
        try:
            action = _json.loads(json_str)
            if isinstance(action, dict) and "type" in action:
                return action, clean
        except _json.JSONDecodeError:
            pass

    # 2. Fallback : JSON inline avec "type"
    m2 = _ACTION_JSON_INLINE_RE.search(text)
    if m2:
        try:
            start = m2.start()
            depth = 0
            end = start
            for i, ch in enumerate(text[start:], start):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            json_str = text[start:end]
            action = _json.loads(json_str)
            if isinstance(action, dict) and "type" in action:
                clean = (text[:start] + text[end:]).strip()
                return action, clean
        except (_json.JSONDecodeError, ValueError):
            pass

    return None, text

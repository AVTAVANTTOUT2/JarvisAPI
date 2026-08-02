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
from api.action_confirmations import (
    cancel_pending_proposal,
    consume_text_confirmation,
    store_pending_proposal,
)
from api.chat_context import _build_enriched_context, _maybe_title_conversation
from database import save_message, update_conversation_activity
from jarvis.security.llm_data_boundary import format_action_result_for_external_llm

logger = logging.getLogger("jarvis")


# ── WebSocket chat ──────────────────────────────────────────

_ACTION_RE = re.compile(r"```action\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)

ACTIONS_WITH_FOLLOWUP = frozenset({
    "terminal",
    "find_file",
    "system_info",
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
    "food_order",
})

# Types d'actions qui peuvent déclencher la boucle agentique (multi-étapes)
AGENTIC_ACTION_TYPES = frozenset({"terminal"})


def _is_agentic_action(action: dict) -> bool:
    """Une boucle terminal ne démarre qu'après un plan serveur confirmé.

    La première passe doit rester dans le pipeline simple afin de construire
    et afficher la liste complète des commandes sans rien exécuter.
    """
    return (
        action.get("type") in AGENTIC_ACTION_TYPES
        and action.get("complex") is True
        and action.get("confirmed") is True
        and bool(action.get("shell_plan_id"))
    )


async def _run_loop_mode_ws(
    ws: WebSocket,
    task: str,
    conversation_id: int,
    *,
    voice_mode: bool = False,
    confirmation_session_id: str,
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
    pending_action = loop_result.get("pending_action")
    if isinstance(pending_action, dict):
        _maybe_store_pending_proposal(
            pending_action,
            conversation_id,
            confirmation_session_id,
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
    confirmation_session_id: str,
) -> dict:
    """Mode /loop sans WebSocket (REST, daemon, iMessage)."""
    context = await _build_enriched_context(task, conversation_id)
    if voice_mode:
        context["voice_mode"] = True

    loop_result = await run_autonomous_loop(task, conversation_id, context)
    pending_action = loop_result.get("pending_action")
    if isinstance(pending_action, dict):
        _maybe_store_pending_proposal(
            pending_action,
            conversation_id,
            confirmation_session_id,
        )
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


def _cancel_pending_proposal(
    conversation_id: int,
    proposal_id: str,
    confirmation_session_id: str,
) -> bool:
    """Annule la proposition de la conversation et révoque son plan shell."""
    return cancel_pending_proposal(
        proposal_id,
        conversation_id=conversation_id,
        session_id=confirmation_session_id,
    )


def _pop_pending_action_if_confirmed(
    text: str,
    conversation_id: int,
    confirmation_session_id: str,
) -> dict | None:
    """Retire et retourne l'action pending si l'utilisateur confirme (« oui », « vas-y »…)."""
    action = consume_text_confirmation(
        text,
        conversation_id=conversation_id,
        session_id=confirmation_session_id,
    )
    if action:
        logger.info(
            "[pending] Confirmation exacte détectée → exécution de %s",
            action.get("type"),
        )
    return action


def _maybe_store_pending_proposal(
    action: dict,
    conversation_id: int,
    confirmation_session_id: str,
) -> dict:
    """Stocke une proposition d'action en attente de confirmation de l'utilisateur.

    Quand JARVIS dit « Veux-tu que je fasse X ? » avec un bloc action,
    on mémorise l'action pour que si l'utilisateur répond « oui » / « vas-y »
    au message suivant, l'action soit exécutée immédiatement.
    """
    return store_pending_proposal(
        action,
        conversation_id=conversation_id,
        session_id=confirmation_session_id,
    )


async def _check_pending_proposal(
    ws, text: str, conversation_id: int, confirmation_session_id: str,
) -> dict | None:
    """Vérifie si l'utilisateur confirme une proposition en attente.

    Retourne le résultat de l'action si confirmée, None sinon.
    """
    action = _pop_pending_action_if_confirmed(
        text,
        conversation_id,
        confirmation_session_id,
    )
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
    """Compatibilité interne vers la frontière unique de résultats d'action."""
    return format_action_result_for_external_llm(action, action_result)


def _extract_action_from_text(text: str) -> tuple[dict | None, str]:
    """Extrait un bloc ```action {JSON}``` d'une réponse — tolérant au format.

    Accepte uniquement un bloc `````action`` explicite, avec ou sans retour
    à la ligne avant le JSON. Un exemple JSON inline n'est jamais exécutable.

    Retourne (action_dict, texte_propre) ou (None, text).
    """
    import json as _json

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

    return None, text

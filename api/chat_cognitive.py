"""Préambule cognitif du pipeline chat (WS + REST) — délégation Cursor.

Le routeur cognitif est consulté AVANT l'orchestrateur :
- tâche technique réelle → job Cursor (worktree isolé) + accusé immédiat ;
- sinon → None, le pipeline classique continue (l'intent est retourné pour
  les diagnostics).

Chemin partagé par ``api/ws_messages._process_message`` et
``api/chat_processing._process_message_internal`` — un seul routeur, pas de
classificateurs contradictoires.
"""

from __future__ import annotations

import logging
from typing import Any

import config
from jarvis.cognitive import route_request
from jarvis.cognitive.models import TaskIntent

logger = logging.getLogger("jarvis")

_CHAT_ACK_CURSOR = (
    "Je m'en occupe, Monsieur. La tâche est confiée à Cursor dans une branche "
    "isolée — je vous rends compte dès que les tests sont terminés."
)


def route_chat_text(text: str, *, voice_mode: bool = False) -> TaskIntent:
    """Route un message chat (source de vérité unique pour le texte)."""
    return route_request(text, interaction_mode="voice" if voice_mode else "chat")


async def maybe_delegate_chat_to_cursor(
    text: str,
    conversation_id: int,
    *,
    intent: TaskIntent | None = None,
    interaction_mode: str = "chat",
) -> dict[str, Any] | None:
    """Si l'intent est technique → enqueue Cursor et retourne la réponse d'ack.

    Retourne None si le message ne relève pas de Cursor (pipeline classique)
    ou si la délégation échoue (l'orchestrateur reprend la main avec une
    explication honnête plutôt qu'un silence).
    """
    intent = intent or route_request(text, interaction_mode=interaction_mode)
    if intent.execution_type != "cursor":
        return None
    if not getattr(config, "CURSOR_DELEGATION_ENABLED", True):
        return None

    try:
        from integrations.cursor_delegation import cursor_delegation

        job = await cursor_delegation.enqueue(
            title=text[:120],
            user_request=text,
            template_id=intent.template_id or "feature_implementation",
            interaction_mode=interaction_mode,
            routing=intent.to_diagnostic(),
            auto_start=True,
        )
    except Exception as exc:
        logger.warning("[chat_cognitive] délégation Cursor impossible : %s", exc)
        return {
            "handled": False,
            "error": str(exc),
            "routing": intent.to_diagnostic(),
        }

    job_id = job.get("job_id")
    if interaction_mode in ("voice", "android") and intent.voice_ack:
        # À l'oral : phrase courte, les détails restent dans l'interface.
        ack = intent.voice_ack
    else:
        ack = (
            f"{_CHAT_ACK_CURSOR}\n\n"
            f"Job `{job_id}` — template `{job.get('prompt_template')}` "
            f"v{job.get('template_version')}. Suivi dans l'onglet Délégations."
        )
    try:
        from database import save_message

        save_message(conversation_id, "assistant", ack, agent="cognitive", cost=0.0)
    except Exception as exc:
        logger.debug("[chat_cognitive] save ack : %s", exc)

    return {
        "handled": True,
        "text": ack,
        "emotion": "neutral",
        "job_id": job_id,
        "job": job,
        "routing": intent.to_diagnostic(),
    }

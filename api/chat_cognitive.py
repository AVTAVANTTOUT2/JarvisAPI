"""Préambule cognitif du pipeline chat (WS + REST) — délégation Cursor.

Le routeur cognitif est consulté AVANT l'orchestrateur :
- tâche technique réelle → proposition Cursor (attente confirmation) ;
- sinon → None, le pipeline classique continue (l'intent est retourné pour
  les diagnostics).

Chemin partagé par ``api/ws_messages._process_message`` et
``api/chat_processing._process_message_internal`` — un seul routeur, pas de
classificateurs contradictoires.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import config
from jarvis.cognitive import route_request
from jarvis.cognitive.models import TaskIntent
from jarvis.security.redaction import public_cursor_job_view

logger = logging.getLogger("jarvis")

_CHAT_ACK_CURSOR_PROPOSAL = (
    "J'ai préparé la délégation à Cursor dans une branche isolée. "
    "Confirmez pour démarrer — dites « lance » / « vas-y », ou validez dans "
    "l'onglet Délégations."
)

_CONFIRM_RE = re.compile(
    r"^\s*(lance|vas[- ]y|confirme|go|ok\s+lance|démarre|demarre)\s*[.!]?\s*$",
    re.I,
)


def route_chat_text(text: str, *, voice_mode: bool = False) -> TaskIntent:
    """Route un message chat (source de vérité unique pour le texte)."""
    return route_request(text, interaction_mode="voice" if voice_mode else "chat")


def is_cursor_confirmation_phrase(text: str) -> bool:
    return bool(_CONFIRM_RE.match((text or "").strip()))


async def maybe_confirm_pending_cursor(
    text: str,
    conversation_id: int,
    *,
    interaction_mode: str = "chat",
) -> dict[str, Any] | None:
    """Si l'utilisateur confirme (« lance ») → démarre le job en attente."""
    if not is_cursor_confirmation_phrase(text):
        return None
    try:
        from integrations.cursor_delegation import cursor_delegation
        from database.cursor_jobs import list_jobs_by_statuses

        pending = list_jobs_by_statuses(("awaiting_confirmation", "proposal"))
        if interaction_mode:
            filtered = [
                j for j in pending if j.get("interaction_mode") == interaction_mode
            ]
            # Fallback : n'importe quel pending si mode exact absent
            pending = filtered or pending
        if not pending:
            return None
        latest = pending[-1]  # ASC → dernier = plus récent
        job = await cursor_delegation.confirm(latest["job_id"])
    except Exception as exc:
        logger.warning("[chat_cognitive] confirm Cursor : %s", exc)
        return None

    ack = (
        f"C'est parti, Monsieur. Job `{job.get('job_id')}` démarré — "
        "je vous rends compte dès que les tests sont terminés."
    )
    try:
        from database import save_message

        save_message(conversation_id, "assistant", ack, agent="cognitive", cost=0.0)
    except Exception as exc:
        logger.debug("[chat_cognitive] save confirm ack : %s", exc)

    return {
        "handled": True,
        "text": ack,
        "emotion": "neutral",
        "job_id": job.get("job_id"),
        "job": public_cursor_job_view(job),
        "confirmed": True,
    }


async def maybe_delegate_chat_to_cursor(
    text: str,
    conversation_id: int,
    *,
    intent: TaskIntent | None = None,
    interaction_mode: str = "chat",
) -> dict[str, Any] | None:
    """Si l'intent est technique → propose un job Cursor (sans auto-start).

    Retourne None si le message ne relève pas de Cursor (pipeline classique)
    ou si la délégation échoue (l'orchestrateur reprend la main avec une
    explication honnête plutôt qu'un silence).
    """
    # Confirmation d'abord (évite de re-router « lance » comme tâche tech)
    confirmed = await maybe_confirm_pending_cursor(
        text, conversation_id, interaction_mode=interaction_mode
    )
    if confirmed:
        return confirmed

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
            auto_start=False,
            require_confirmation=True,
        )
    except Exception as exc:
        logger.warning("[chat_cognitive] délégation Cursor impossible : %s", exc)
        return {
            "handled": False,
            "error": str(exc),
            "routing": intent.to_diagnostic(),
        }

    job_id = job.get("job_id")
    if interaction_mode in ("voice", "android"):
        ack = (
            intent.voice_ack
            or "J'ai préparé la délégation à Cursor. Dites « lance » pour démarrer."
        )
    else:
        ack = (
            f"{_CHAT_ACK_CURSOR_PROPOSAL}\n\n"
            f"Job `{job_id}` — template `{job.get('prompt_template')}` "
            f"v{job.get('template_version')} — statut `{job.get('status')}`."
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
        "job": public_cursor_job_view(job),
        "routing": intent.to_diagnostic(),
        "awaiting_confirmation": True,
    }

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

import json
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


def _cursor_job_routing(job: dict[str, Any]) -> dict[str, Any]:
    """Lit le routing persisté d'un job Cursor (dict ou JSON legacy)."""
    routing = job.get("routing") or job.get("routing_json") or {}
    if isinstance(routing, str):
        try:
            routing = json.loads(routing)
        except json.JSONDecodeError:
            return {}
    return routing if isinstance(routing, dict) else {}


def _cursor_job_conversation_id(job: dict[str, Any]) -> int | None:
    """Conversation liée à la proposition, si le job a été créé depuis le chat/voix."""
    raw = _cursor_job_routing(job).get("conversation_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def resolve_pending_cursor_confirmation(
    conversation_id: int,
    interaction_mode: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Résout un job Cursor sans repli global et explique tout refus.

    Sans filtre par conversation, « lance » dans une discussion peut démarrer
    un worktree préparé dans une autre, ou un job vocal depuis le chat.
    """
    from database.cursor_jobs import list_jobs_by_statuses

    pending = list_jobs_by_statuses(("awaiting_confirmation", "proposal"))
    mode_pending = [
        job
        for job in pending
        if job.get("interaction_mode") == interaction_mode
    ]
    scoped = [
        job
        for job in mode_pending
        if _cursor_job_conversation_id(job) == int(conversation_id)
    ]
    if scoped:
        return scoped[-1], None
    if any(_cursor_job_conversation_id(job) is None for job in mode_pending):
        return None, "cursor_confirmation_legacy_unscoped"
    if mode_pending:
        return None, "cursor_confirmation_different_conversation"
    if pending:
        return None, "cursor_confirmation_different_mode"
    return None, "cursor_confirmation_missing"


def resolve_pending_cursor_job_for_confirmation(
    conversation_id: int,
    interaction_mode: str,
) -> dict[str, Any] | None:
    """Compatibilité : retourne uniquement le job confirmable, le cas échéant."""
    job, _reason = resolve_pending_cursor_confirmation(
        conversation_id,
        interaction_mode,
    )
    return job


def cursor_confirmation_unavailable_message(reason: str | None) -> str:
    """Message explicite et sans fuite d'information pour une confirmation refusée."""
    if reason == "cursor_confirmation_legacy_unscoped":
        return (
            "Cette ancienne proposition Cursor n'est liée à aucune conversation. "
            "Rien n'a été lancé ; reformulez la demande pour créer une proposition sûre."
        )
    if reason == "cursor_confirmation_different_conversation":
        return (
            "Aucune proposition Cursor n'attend confirmation dans cette conversation. "
            "Rien n'a été lancé."
        )
    if reason == "cursor_confirmation_different_mode":
        return (
            "La proposition Cursor en attente appartient à un autre mode d'interaction. "
            "Rien n'a été lancé."
        )
    return "Aucune proposition Cursor n'attend confirmation ici. Rien n'a été lancé."


def _cursor_confirmation_response(
    conversation_id: int,
    text: str,
    *,
    job: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Construit et persiste une réponse terminale de confirmation Cursor."""
    try:
        from database import save_message

        save_message(conversation_id, "assistant", text, agent="cognitive", cost=0.0)
    except Exception as exc:
        logger.debug("[chat_cognitive] save confirm ack : %s", exc)

    result: dict[str, Any] = {
        "handled": True,
        "text": text,
        "emotion": "neutral",
        "confirmed": job is not None,
    }
    if job is not None:
        result.update(
            job_id=job.get("job_id"),
            job=public_cursor_job_view(job),
        )
    if error is not None:
        result["error"] = error
    return result


def should_run_cursor_cognitive_path(
    text: str,
    intent: TaskIntent,
    conversation_id: int,
    confirmation_session_id: str,
) -> bool:
    """Vrai si le message doit emprunter maybe_delegate_chat_to_cursor.

    Les confirmations (« lance », « vas-y ») ne sont pas classées ``cursor``
    par le routeur, mais doivent quand même confirmer un job en attente.
    Une proposition shell/food/terminal liée à la session prime (parité voix).
    """
    if intent.execution_type == "cursor":
        return True
    if not is_cursor_confirmation_phrase(text):
        return False
    from api.action_confirmations import peek_pending_proposal

    return (
        peek_pending_proposal(
            conversation_id=conversation_id,
            session_id=confirmation_session_id,
        )
        is None
    )


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

        latest, reason = resolve_pending_cursor_confirmation(
            conversation_id,
            interaction_mode,
        )
        if not latest:
            return _cursor_confirmation_response(
                conversation_id,
                cursor_confirmation_unavailable_message(reason),
                error=reason,
            )
        job = await cursor_delegation.confirm(latest["job_id"])
    except Exception as exc:
        logger.warning("[chat_cognitive] confirm Cursor : %s", exc)
        return _cursor_confirmation_response(
            conversation_id,
            "Je n'ai pas pu confirmer la proposition Cursor. Rien n'a été lancé.",
            error="cursor_confirmation_failed",
        )

    ack = (
        f"C'est parti, Monsieur. Job `{job.get('job_id')}` démarré — "
        "je vous rends compte dès que les tests sont terminés."
    )
    return _cursor_confirmation_response(conversation_id, ack, job=job)


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

        routing = dict(intent.to_diagnostic())
        routing["conversation_id"] = int(conversation_id)

        job = await cursor_delegation.enqueue(
            title=text[:120],
            user_request=text,
            template_id=intent.template_id or "feature_implementation",
            interaction_mode=interaction_mode,
            routing=routing,
            auto_start=False,
            require_confirmation=True,
        )
    except Exception as exc:
        logger.warning("[chat_cognitive] délégation Cursor impossible : %s", exc)
        return {
            "handled": False,
            "error": "cursor_delegation_failed",
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

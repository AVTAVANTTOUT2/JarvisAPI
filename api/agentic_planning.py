"""Planification agentique fail-closed avant toute exécution."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from jarvis.agentic.turn_context import (
    AGENTIC_ROUTING_METADATA_KEY,
    SNAPSHOT_METADATA_KEY,
    TurnKnowledgeSnapshot,
)

logger = logging.getLogger(__name__)


def _persist_assistant(
    save_message_fn: Callable[..., Any],
    conversation_id: int,
    text: str,
) -> None:
    try:
        save_message_fn(
            conversation_id,
            "assistant",
            text,
            agent="agentic",
            model="runtime",
            tokens_in=0,
            tokens_out=0,
            cost=0.0,
        )
    except Exception:
        logger.exception("persistance de l'accusé agentique impossible")


def _planning_unavailable_response(
    *,
    conversation_id: int,
    persist_assistant: bool,
    save_message_fn: Callable[..., Any],
    snapshot: TurnKnowledgeSnapshot | None,
) -> dict[str, Any]:
    text = (
        "Je n'ai pas pu enregistrer le plan. Rien n'a été lancé ; "
        "réessayez lorsque le service de planification sera disponible."
    )
    if persist_assistant:
        _persist_assistant(save_message_fn, conversation_id, text)
    response: dict[str, Any] = {
        "text": text,
        "emotion": "neutral",
        "action": None,
        "action_result": {
            "ok": False,
            "accepted": False,
            "awaiting_plan_approval": False,
            "error": "planning_unavailable",
        },
        "agent": "agentic",
        "model": "runtime",
        "cost": 0.0,
    }
    if snapshot is not None:
        response["knowledge"] = snapshot.public_payload()
    return response


async def plan_instead_of_running(
    request: str,
    conversation_id: int,
    *,
    channel: str,
    voice_mode: bool,
    persist_assistant: bool,
    save_message_fn: Callable[..., Any],
    snapshot: TurnKnowledgeSnapshot | None = None,
    classification: Any | None = None,
    capability_profile: Any | None = None,
    origin: str = "user",
    device: str | None = None,
    locale: str | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    """Crée un plan durable ou refuse explicitement de lancer le run.

    La porte d'approbation est une frontière de sécurité : une indisponibilité
    du planner ou de sa persistance ne doit jamais devenir une autorisation
    implicite de démarrage.
    """

    try:
        from jarvis.task_control.ingest import create_task_from_user_request

        metadata: dict[str, Any] = {}
        if snapshot is not None:
            metadata[SNAPSHOT_METADATA_KEY] = snapshot.to_metadata()
        if classification is not None and capability_profile is not None:
            metadata[AGENTIC_ROUTING_METADATA_KEY] = {
                "category": classification.category.value,
                "reason": classification.reason,
                "capability_profile_id": capability_profile.profile_id,
                "permissions": list(capability_profile.default_permissions),
                "origin": origin,
                "device": device,
                "locale": locale,
                "timezone": timezone_name,
            }
        created = await create_task_from_user_request(
            request,
            channel="voice" if voice_mode else channel,
            conversation_id=str(conversation_id),
            metadata=metadata or None,
            planning_context=(
                snapshot.planning_context() if snapshot is not None else None
            ),
        )
    except Exception:
        logger.exception("pilotage de tâches indisponible")
        created = None

    if created is None:
        return _planning_unavailable_response(
            conversation_id=conversation_id,
            persist_assistant=persist_assistant,
            save_message_fn=save_message_fn,
            snapshot=snapshot,
        )

    acknowledgement = (
        "J'ai préparé un plan. Il attend votre validation avant tout démarrage."
        if voice_mode
        else "Un plan est prêt. Ouvrez la tâche pour l'accepter, le refuser ou demander une correction."
    )
    if persist_assistant:
        _persist_assistant(save_message_fn, conversation_id, acknowledgement)
    response: dict[str, Any] = {
        "text": acknowledgement,
        "emotion": "neutral",
        "action": {"type": "task_control_task", "task_id": created["task_id"]},
        "action_result": {
            "ok": True,
            "accepted": True,
            "awaiting_plan_approval": True,
            "task_id": created["task_id"],
            "status": created["status"],
        },
        "task_control": {
            "task_id": created["task_id"],
            "status": created["status"],
        },
        "agent": "agentic",
        "model": "runtime",
        "cost": 0.0,
    }
    if snapshot is not None:
        response["knowledge"] = snapshot.public_payload()
    return response

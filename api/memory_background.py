"""Traitement mémoire différé après fermeture d'une conversation."""

from __future__ import annotations

import logging

from agents.memory import memory_agent

logger = logging.getLogger("jarvis")


async def _run_memory_in_background(conversation_id: int) -> None:
    """Traite la conversation par l'agent mémoire — silencieux côté UX."""
    try:
        applied = await memory_agent.process_conversation(conversation_id)
        if applied:
            logger.info(f"[memory bg] conv {conversation_id} → {applied}")
    except Exception as e:
        logger.error(f"[memory bg] conv {conversation_id} : {e}")




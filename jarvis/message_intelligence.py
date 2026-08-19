"""
Pipeline d'intelligence sur messages iMessage.

Politique LLM 2026 : aucun LLM local de raisonnement.
Flux : messages bruts → DeepSeek Flash → stockage.

Les noms, numéros et corps de messages partent tels quels.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from jarvis.exceptions import JARVISError
from jarvis.pii.boundary import DataBoundary
from jarvis.router import JARVISRouter

logger = logging.getLogger("jarvis.message_intelligence")

_router: Optional[JARVISRouter] = None


def _ensure_components() -> JARVISRouter:
    """Initialise les composants partagés au premier appel (lazy init)."""
    global _router
    if _router is None:
        _router = JARVISRouter(boundary=DataBoundary())
    return _router


async def analyze_recent_messages(
    since_id: int, batch_size: int = 50
) -> dict[str, Any]:
    """Analyse les messages récents via DeepSeek, texte intact.

    Flux :
    1. Récupère messages bruts depuis la DB
    2. DeepSeek Flash résume + propose actions
    3. Stocke en DB (table message_insights)
    """
    import database

    raw_messages = database.get_messages_since(since_id, limit=batch_size)
    return await analyze_message_batch(
        raw_messages,
        since_id=since_id,
        source="jarvis_conversation",
    )


async def analyze_message_batch(
    raw_messages: list[dict[str, Any]],
    *,
    since_id: int,
    source: str,
) -> dict[str, Any]:
    """Analyse un lot déjà lu depuis sa source canonique.

    ``since_id`` appartient explicitement à ``source``. Cette séparation évite
    qu'un ROWID Apple soit interprété comme un identifiant de la table JARVIS
    ``messages``.
    """
    if not raw_messages:
        return {"status": "no_new_messages", "source": source}

    def _label(message: dict[str, Any]) -> str:
        if "role" in message:
            return str(message.get("role") or "?")
        if message.get("is_from_me"):
            return "moi"
        return str(message.get("handle") or "contact")

    def _content(message: dict[str, Any]) -> str:
        return str(message.get("content") or message.get("text") or "")

    raw_text = "\n".join(
        f"{_label(message)}: {_content(message)}" for message in raw_messages
    )

    router = _ensure_components()

    deepseek_prompt = (
        "Analyse ce lot de messages.\n\n"
        "Propose :\n"
        "1. Annonces pertinentes (max 3)\n"
        "2. Tâches à créer si action implicite (titre + priorité)\n"
        "3. Suggestions proactives (max 2)\n\n"
        "JSON strict : "
        '{"announcements": [...], "tasks": [...], "suggestions": [...]}\n\n'
        f"Messages :\n{raw_text}"
    )

    try:
        deepseek_response = await router.deepseek.generate(
            prompt=deepseek_prompt,
            system="Tu analyses des messages. JSON uniquement.",
            max_tokens=800,
        )
    except JARVISError as e:
        logger.error("[message_intelligence] DeepSeek erreur : %s", e)
        return {"status": "deepseek_error", "error": str(e)}

    import database

    insight_id = database.save_message_insight(
        since_id=since_id,
        raw_response=deepseek_response,
        message_count=len(raw_messages),
    )
    return {
        "status": "ok",
        "insight_id": insight_id,
        "result": deepseek_response,
        "backend": "deepseek_flash",
        "source": source,
    }

"""
Pipeline d'intelligence sur messages iMessage.

Politique LLM 2026 : aucun LLM local de raisonnement.
Flux : messages bruts → anonymisation PII → DeepSeek Flash → dé-anonymisation → stockage.

Le texte brut des messages ne quitte JAMAIS la machine non anonymisé.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from jarvis.exceptions import JARVISError
from jarvis.pii.anonymizer import PIIAnonymizer
from jarvis.pii.boundary import DataBoundary, DataLeakError
from jarvis.router import JARVISRouter

logger = logging.getLogger("jarvis.message_intelligence")

_router: Optional[JARVISRouter] = None
_anonymizer: Optional[PIIAnonymizer] = None
_boundary: Optional[DataBoundary] = None


def _ensure_components() -> JARVISRouter:
    """Initialise les composants partagés au premier appel (lazy init)."""
    global _router, _anonymizer, _boundary
    if _router is None:
        _anonymizer = PIIAnonymizer()
        _boundary = DataBoundary()
        _router = JARVISRouter(anonymizer=_anonymizer, boundary=_boundary)
    return _router


async def analyze_recent_messages(
    since_id: int, batch_size: int = 50
) -> dict[str, Any]:
    """Analyse les messages récents via DeepSeek (PII anonymisée).

    Flux :
    1. Récupère messages bruts depuis la DB (local uniquement)
    2. Anonymise le lot (DeepSeek ne voit jamais le brut)
    3. DeepSeek Flash résume + propose actions
    4. Dé-anonymise
    5. Stocke en DB (table message_insights)
    """
    import database

    raw_messages = database.get_messages_since(since_id, limit=batch_size)
    if not raw_messages:
        return {"status": "no_new_messages"}

    raw_text = "\n".join(
        f"{m.get('role', '?')}: {m.get('content', '')}" for m in raw_messages
    )

    router = _ensure_components()
    assert _anonymizer is not None and _boundary is not None

    anon = _anonymizer.anonymize(raw_text)
    try:
        _boundary.check(anon.anonymized_text)
    except DataLeakError as e:
        logger.error("[message_intelligence] Fuite interceptée : %s", e)
        return {"status": "boundary_violation", "error": str(e)}

    deepseek_prompt = (
        "Analyse ce lot de messages pseudonymisés. "
        "Les noms et coordonnées sont des tokens (ex: [PERSON_1]). "
        "Garde STRICTEMENT ces tokens intacts.\n\n"
        "Propose :\n"
        "1. Annonces pertinentes (max 3)\n"
        "2. Tâches à créer si action implicite (titre + priorité)\n"
        "3. Suggestions proactives (max 2)\n\n"
        "JSON strict : "
        '{"announcements": [...], "tasks": [...], "suggestions": [...]}\n\n'
        f"Messages :\n{anon.anonymized_text}"
    )

    try:
        deepseek_response = await router.deepseek.generate(
            prompt=deepseek_prompt,
            system=(
                "Tu analyses des messages anonymisés. "
                "Ne tente jamais de deviner les identités. JSON uniquement."
            ),
            max_tokens=800,
        )
    except JARVISError as e:
        logger.error("[message_intelligence] DeepSeek erreur : %s", e)
        return {"status": "deepseek_error", "error": str(e)}

    final_response = _anonymizer.deanonymize(deepseek_response, anon.mapping)
    anon.mapping.clear()

    insight_id = database.save_message_insight(
        since_id=since_id,
        raw_response=final_response,
        message_count=len(raw_messages),
    )
    return {
        "status": "ok",
        "insight_id": insight_id,
        "result": final_response,
        "backend": "deepseek_flash_anonymized",
    }

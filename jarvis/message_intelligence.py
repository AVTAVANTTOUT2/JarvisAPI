"""
Pipeline d'intelligence sur messages iMessage.
Flux obligatoire : messages bruts → LocalBackend (résumé/extraction) →
PIIAnonymizer (pseudonymisation) → DeepSeek (analyse) → dé-anonymisation → stockage.

Le texte brut des messages ne quitte JAMAIS la machine. DeepSeek ne voit que
des tokens [PERSON_N], [PHONE_N], [EMAIL_N], etc.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from jarvis.exceptions import JARVISError
from jarvis.pii.anonymizer import PIIAnonymizer
from jarvis.pii.boundary import DataBoundary, DataLeakError
from jarvis.router import JARVISRouter

logger = logging.getLogger("jarvis.message_intelligence")

# Instance partagée du routeur dual-LLM (local + DeepSeek).
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
    """Analyse les messages récents via le pipeline d'intelligence.

    Flux obligatoire :
    1. Récupère messages bruts depuis la DB (local uniquement)
    2. LocalBackend résume/extrait les sujets pertinents (toujours local)
    3. Anonymise le résumé local (pas le texte brut — réduit la surface PII)
    4. Envoie à DeepSeek pour : annonces, propositions de tâches, suggestions
    5. Dé-anonymise la réponse DeepSeek
    6. Stocke en DB (table message_insights)

    Args:
        since_id: ID du dernier message déjà traité.
        batch_size: Nombre max de messages à analyser par lot (défaut 50).

    Returns:
        Dict {"status": "ok", "insight_id": int, "result": ...}
        ou {"status": "no_new_messages" | "local_backend_error" | "deepseek_error", ...}
    """
    import database

    raw_messages = database.get_messages_since(since_id, limit=batch_size)
    if not raw_messages:
        return {"status": "no_new_messages"}

    raw_text = "\n".join(
        f"{m.get('role', '?')}: {m.get('content', '')}" for m in raw_messages
    )

    router = _ensure_components()

    # ── Étape 1 : résumé local obligatoire — DeepSeek ne voit jamais ce texte ──
    local_summary_prompt = (
        "/think\n"
        "Résume les sujets et infos actionnables de ces messages. "
        "Reste factuel, garde les noms tels quels, ne fais pas de suppositions. "
        "Liste uniquement :\n"
        "- Les annonces ou nouvelles importantes\n"
        "- Les actions implicites (quelqu'un demande quelque chose)\n"
        "- Les infos contextuelles utiles pour l'utilisateur\n\n"
        f"{raw_text}"
    )
    try:
        local_summary = await router.local.generate(
            prompt=local_summary_prompt,
            max_tokens=800,
        )
    except JARVISError as e:
        logger.error("[message_intelligence] LocalBackend échec : %s", e)
        return {"status": "local_backend_error", "error": str(e)}

    if not local_summary.strip():
        logger.info("[message_intelligence] Résumé local vide — pas d'actionnable détecté.")
        return {"status": "no_actionable"}

    # ── Étape 2 : anonymisation du résumé ──
    # On anonymise UNIQUEMENT le résumé, pas le texte brut.
    # Le texte brut est déjà détruit (variable locale, hors scope).
    anonymized_summary, mapping = _anonymizer.anonymize(local_summary)  # type: ignore[union-attr]

    # ── Garde-fou : vérifie qu'aucun pattern interdit ne fuit ──
    try:
        _boundary.check(anonymized_summary)  # type: ignore[union-attr]
    except DataLeakError as e:
        logger.error("[message_intelligence] Fuite interceptée — requête DeepSeek annulée : %s", e)
        return {"status": "boundary_violation", "error": str(e)}

    # ── Étape 3 : DeepSeek analyse le résumé anonymisé ──
    deepseek_prompt = (
        "Analyse ce résumé de messages pseudonymisés. "
        "Les noms et coordonnées ont été remplacés par des tokens (ex: [PERSON_1]). "
        "Garde STRICTEMENT ces tokens intacts dans ta réponse.\n\n"
        "Propose :\n"
        "1. Annonces pertinentes à faire à l'utilisateur (max 3, format court)\n"
        "2. Tâches à créer si une action est implicite (titre + priorité high/medium/low)\n"
        "3. Suggestions proactives (max 2)\n\n"
        "Réponds en JSON strict sans texte hors JSON :\n"
        '{"announcements": [...], "tasks": [...], "suggestions": [...]}\n\n'
        f"Résumé : {anonymized_summary}"
    )

    try:
        deepseek_response = await router.deepseek.generate(
            prompt=deepseek_prompt,
            system=(
                "Tu analyses des résumés anonymisés. "
                "Ne tente jamais de deviner les identités masquées. "
                "Réponds UNIQUEMENT en JSON."
            ),
            max_tokens=600,
        )
    except JARVISError as e:
        logger.error("[message_intelligence] DeepSeek erreur : %s", e)
        return {"status": "deepseek_error", "error": str(e)}

    # ── Étape 4 : dé-anonymisation — remet les vrais noms ──
    final_response = _anonymizer.deanonymize(deepseek_response, mapping)  # type: ignore[union-attr]

    # ── Étape 5 : stockage en DB ──
    insight_id = database.save_message_insight(
        since_id=since_id,
        raw_response=final_response,
        message_count=len(raw_messages),
    )

    logger.info(
        "[message_intelligence] Insight #%d généré sur %d messages",
        insight_id,
        len(raw_messages),
    )
    return {"status": "ok", "insight_id": insight_id, "result": final_response}

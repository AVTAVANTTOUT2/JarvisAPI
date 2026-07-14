"""Handlers du profil de vie, du journal et des patterns."""

from __future__ import annotations

import logging

from fastapi import HTTPException

import pipeline
from agents.journal import journal_agent
from api.llm_logging import _schedule_llm_log
from database import (
    add_life_profile_entry,
    create_conversation,
    delete_life_profile_entry,
    get_active_patterns,
    get_life_profile,
    get_life_profile_entries,
    get_recent_episodes,
    get_recent_moods,
    save_message,
    update_conversation_activity,
    update_life_profile_entry,
)

logger = logging.getLogger("jarvis")



# ── Phase 5 : Life profile / People / Journal / Patterns ────


async def api_life_profile_get():
    """Retourne le life profile groupé par catégorie + version brute avec ids (pour édition)."""
    return {
        "grouped": get_life_profile(),
        "entries": get_life_profile_entries(),
    }


async def api_life_profile_create(payload: dict):
    category = (payload.get("category") or "").strip()
    content = (payload.get("content") or "").strip()
    if not category or not content:
        raise HTTPException(400, "`category` et `content` requis")
    if category not in ("values", "goals", "fears", "patterns", "strengths"):
        raise HTTPException(400, "Catégorie invalide")

    entry_id = add_life_profile_entry(category, content)
    return {"id": entry_id, "category": category, "content": content}


async def api_life_profile_update(entry_id: int, payload: dict):
    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "`content` requis")
    if not update_life_profile_entry(entry_id, content):
        raise HTTPException(404, "Entrée introuvable")
    return {"id": entry_id, "content": content}


async def api_life_profile_delete(entry_id: int):
    if not delete_life_profile_entry(entry_id):
        raise HTTPException(404, "Entrée introuvable")
    return {"status": "deleted", "id": entry_id}


async def api_life_context_list(active_only: bool = False):
    """Périodes de vie détectées (déménagement, rupture, nouveau travail...).

    ``active_only=true`` ne retourne que les périodes en cours ; par défaut
    l'historique complet (actives + closes) est renvoyé, le plus récent en premier.
    """
    from database import get_active_life_context, get_all_life_context

    return {
        "periods": get_active_life_context() if active_only else get_all_life_context(),
    }


async def api_life_context_create(payload: dict):
    from database import add_life_context

    context_type = (payload.get("context_type") or "").strip()
    description = (payload.get("description") or "").strip()
    if not context_type or not description:
        raise HTTPException(400, "`context_type` et `description` requis")

    context_id = add_life_context(
        context_type, description,
        period_start=payload.get("period_start"),
        period_end=payload.get("period_end"),
        impact_on_mood=payload.get("impact_on_mood"),
        impact_on_productivity=payload.get("impact_on_productivity"),
    )
    return {"id": context_id, "context_type": context_type, "description": description}


async def api_life_context_close(context_id: int):
    from database import close_life_context

    if not close_life_context(context_id):
        raise HTTPException(404, "Période introuvable")
    return {"status": "closed", "id": context_id}


async def api_journal_get():
    """Moods récents + épisodes journal récents."""
    return {
        "moods": get_recent_moods(limit=30),
        "episodes": get_recent_episodes(agent="journal", limit=30),
    }


async def api_journal_post(payload: dict):
    """Envoie une entrée de journal via le pipeline unifié. Retourne réponse + extraction."""
    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "`content` requis")

    try:
        # Créer une conversation temporaire pour le journal
        conv_id = create_conversation(agent="journal")
        save_message(conv_id, "user", content)
        update_conversation_activity(conv_id)

        # Passer par le pipeline unifié — l'orchestrateur route vers JOURNAL automatiquement
        result = await pipeline.process_message_internal(content, conv_id)

        # Extraction JSON des insights via le journal_agent (traitement des données structurées)
        extracted = None
        try:
            extracted = journal_agent._process_journal_data(result.get("text", ""))
            _schedule_llm_log(
                agent="journal",
                action_type="journal_extract",
                payload={"conversation_id": conv_id, "has_extracted": bool(extracted)},
                status="success",
            )
        except Exception:
            _schedule_llm_log(
                agent="journal",
                action_type="journal_extract",
                payload={"conversation_id": conv_id},
                status="error",
            )

        return {
            "response": result.get("text"),
            "extracted": extracted,
            "model": result.get("model"),
            "cost": result.get("cost", 0.0),
        }
    except Exception as e:
        logger.exception("Erreur api_journal_post")
        raise HTTPException(500, f"Erreur journal : {type(e).__name__}: {e}")


async def api_patterns_get():
    return {"patterns": get_active_patterns()}



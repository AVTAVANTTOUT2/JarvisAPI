"""Routes des enregistrements et de la recherche sémantique."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import config
from api.errors import api_error, internal_error
from database import get_recording, get_recordings

router = APIRouter()
logger = logging.getLogger("jarvis")
_LEGACY_SEMANTIC_SOURCE_TYPES = frozenset({"episode", "recording"})
_SEMANTIC_COORDINATOR_MAX_HITS = 8


class SpeakerAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)


@router.get("/api/recordings")
async def api_recordings_list(limit: int = 20):
    """Liste des enregistrements continus (sans transcription complète)."""
    lim = max(1, min(limit, 100))
    try:
        rows = get_recordings(limit=lim)
    except Exception as e:
        logger.exception("api_recordings_list : %s", e)
        raise internal_error(
            "recordings_unavailable", "Enregistrements indisponibles"
        ) from e
    return {"recordings": rows}


@router.get("/api/recordings/{recording_id}")
async def api_recordings_detail(recording_id: int):
    """Détail d'un enregistrement (transcription + synthèse JSON)."""
    row = get_recording(recording_id)
    if not row:
        raise HTTPException(404, "Enregistrement introuvable")
    if config.RECORDING_SUMMARY_ONLY and row.get("transcription"):
        row = {
            **row,
            "transcription": "[omis — RECORDING_SUMMARY_ONLY dans la configuration]",
        }
    return row


@router.get("/api/recordings/{recording_id}/turns")
async def api_recording_turns(recording_id: int):
    """Tours de parole diarisés d'un enregistrement (si capturés — voir DIARIZATION_ENABLED)."""
    from database import get_conversation_turns

    if not get_recording(recording_id):
        raise HTTPException(404, "Enregistrement introuvable")
    return {"turns": get_conversation_turns(recording_id)}


@router.get("/api/recordings/{recording_id}/speakers")
async def api_recording_unlabeled_speakers(recording_id: int):
    """Labels temporaires (« A », « B »…) pas encore associés à une personne."""
    from database import get_unlabeled_speakers

    if not get_recording(recording_id):
        raise HTTPException(404, "Enregistrement introuvable")
    return {"unlabeled_speakers": get_unlabeled_speakers(recording_id)}


@router.post("/api/recordings/{recording_id}/speakers/{label}/assign")
async def api_recording_assign_speaker(
    recording_id: int,
    label: str,
    body: SpeakerAssignmentRequest,
):
    """Répond à « qui était la personne {label} ? » — associe le label à une personne
    (existante ou nouvellement créée par nom)."""
    from database import assign_speaker_to_person, get_db, get_person

    name = body.name
    if not get_recording(recording_id):
        raise HTTPException(404, "Enregistrement introuvable")

    person = get_person(name)
    if person:
        person_id = person["id"]
    else:
        with get_db() as conn:
            cur = conn.execute("INSERT INTO people (name) VALUES (?)", (name,))
            person_id = cur.lastrowid

    updated = assign_speaker_to_person(recording_id, label, person_id)
    if updated == 0:
        raise HTTPException(404, f"Aucun tour de parole pour le label « {label} »")
    return {"ok": True, "person_id": person_id, "name": name, "turns_updated": updated}


@router.get("/api/memory/search-semantic")
async def api_memory_search_semantic(
    q: str, limit: int = 10, source_type: str | None = None
):
    """Recherche la mémoire via le coordinateur, au format historique de l'API.

    L'ancien index vectoriel ne couvrait que les épisodes et enregistrements ;
    cette frontière explicite est conservée. Le coordinateur borne actuellement
    ses réponses à huit hits, même lorsque le ``limit`` historique est supérieur.
    """
    if not q or not q.strip():
        raise HTTPException(400, "`q` requis")
    result_limit = int(limit)
    if result_limit <= 0:
        return {"results": []}
    requested_sources = tuple(sorted(_LEGACY_SEMANTIC_SOURCE_TYPES))
    if source_type is not None:
        normalized_source = source_type.strip().lower()
        if normalized_source not in _LEGACY_SEMANTIC_SOURCE_TYPES:
            return {"results": []}
        requested_sources = (normalized_source,)
    try:
        from jarvis.retrieval import RetrievalRequest, search_knowledge

        retrieval = await asyncio.to_thread(
            search_knowledge,
            RetrievalRequest(
                query=q.strip(),
                interaction_mode="legacy_semantic_api",
                source_types=requested_sources,
                max_candidates=20,
                max_hits=min(result_limit, _SEMANTIC_COORDINATOR_MAX_HITS),
                char_budget=8_000,
            ),
        )
    except Exception as exc:
        logger.warning("[semantic-search] coordinateur indisponible : %s", exc)
        raise api_error(
            503,
            "semantic_search_unavailable",
            "Recherche sémantique indisponible",
        ) from exc
    if retrieval.status == "unavailable":
        raise api_error(
            503,
            "semantic_search_unavailable",
            "Recherche sémantique indisponible",
        )
    results = [
        {
            "source_type": hit.source_type,
            "source_id": int(hit.source_id)
            if hit.source_id.isdigit()
            else hit.source_id,
            "text_preview": hit.excerpt[:500],
            "score": round(float(hit.score), 4),
        }
        for hit in retrieval.hits[:result_limit]
        if hit.source_type in _LEGACY_SEMANTIC_SOURCE_TYPES
    ]
    return {"results": results}

"""Routes des enregistrements et de la recherche sémantique."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

import config
from database import get_recording, get_recordings

router = APIRouter()
logger = logging.getLogger("jarvis")


@router.get("/api/recordings")
async def api_recordings_list(limit: int = 20):
    """Liste des enregistrements continus (sans transcription complète)."""
    lim = max(1, min(limit, 100))
    try:
        rows = get_recordings(limit=lim)
    except Exception as e:
        logger.exception("api_recordings_list : %s", e)
        raise HTTPException(500, str(e)) from e
    return {"recordings": rows}


@router.get("/api/recordings/{recording_id}")
async def api_recordings_detail(recording_id: int):
    """Détail d'un enregistrement (transcription + synthèse JSON)."""
    row = get_recording(recording_id)
    if not row:
        raise HTTPException(404, "Enregistrement introuvable")
    if config.RECORDING_SUMMARY_ONLY and row.get("transcription"):
        row = {**row, "transcription": "[omis — RECORDING_SUMMARY_ONLY dans la configuration]"}
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
async def api_recording_assign_speaker(recording_id: int, label: str, body: dict):
    """Répond à « qui était la personne {label} ? » — associe le label à une personne
    (existante ou nouvellement créée par nom)."""
    from database import assign_speaker_to_person, get_db, get_person

    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "`name` requis")
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
async def api_memory_search_semantic(q: str, limit: int = 10, source_type: str | None = None):
    """Recherche sémantique (similarité de sens, pas seulement mots-clés) sur la mémoire indexée."""
    if not q or not q.strip():
        raise HTTPException(400, "`q` requis")
    try:
        from scripts.semantic_search import SemanticSearchUnavailable, semantic_search

        results = await asyncio.to_thread(semantic_search, q.strip(), limit, source_type)
    except SemanticSearchUnavailable as e:
        raise HTTPException(503, str(e)) from e
    return {"results": results}

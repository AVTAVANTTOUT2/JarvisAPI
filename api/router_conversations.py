"""Routes des conversations et de leurs documents."""

from __future__ import annotations

import logging
from pathlib import Path

import fitz
from fastapi import APIRouter, Body, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from database import (
    delete_conversation,
    get_conversation_detail,
    get_conversations,
    save_conversation_document,
    save_school_document,
    search_conversations,
    update_conversation,
)
from jarvis.document_privacy import (
    DocumentCloudBlocked,
    ensure_cloud_summary_allowed,
    get_document_privacy_policy,
    set_document_strict_local,
    summarize_document,
)
from jarvis.uploads import (
    CONVERSATION_EXTENSIONS,
    UploadRejected,
    remove_managed_upload,
    store_upload,
)

router = APIRouter()
logger = logging.getLogger("jarvis")


class DocumentPrivacyUpdate(BaseModel):
    strict_local: bool


# ── Conversations enrichies ──────────────────────────────────


@router.get("/api/privacy/documents")
async def api_document_privacy_get():
    """Décrit les traitements locaux/cloud appliqués aux documents."""
    return get_document_privacy_policy()


@router.put("/api/privacy/documents")
async def api_document_privacy_update(body: DocumentPrivacyUpdate):
    """Active ou désactive la possibilité de consentir à un résumé cloud."""
    set_document_strict_local(body.strict_local)
    return {"ok": True, **get_document_privacy_policy()}


@router.get("/api/conversations/search")
async def api_conversations_search(q: str = "", limit: int = 20):
    """Recherche dans titres et messages de toutes les conversations."""
    if not q.strip():
        return {"results": [], "count": 0}
    results = search_conversations(q.strip(), limit=limit)
    return {"results": results, "count": len(results)}


@router.get("/api/conversations")
async def api_conversations_list(archived: bool = False, limit: int = 50):
    """Liste des conversations triées par dernière activité."""
    convs = get_conversations(limit=limit, archived=archived)
    return {"conversations": convs}


@router.get("/api/conversations/{conv_id}")
async def api_conversation_get(conv_id: int):
    """Détail d'une conversation (messages + documents)."""
    conv = get_conversation_detail(conv_id)
    if not conv:
        return JSONResponse(status_code=404, content={"error": "Conversation non trouvée"})
    return conv


@router.patch("/api/conversations/{conv_id}")
async def api_conversation_update(conv_id: int, body: dict = Body(default_factory=dict)):
    """Met à jour les métadonnées d'une conversation (titre, pinned, archived…)."""
    allowed = {"title", "pinned", "archived", "tags"}
    fields = {k: v for k, v in body.items() if k in allowed}
    if not fields:
        raise HTTPException(400, "Aucun champ modifiable fourni")
    if not update_conversation(conv_id, **fields):
        raise HTTPException(404, "Conversation non trouvée")
    return {"ok": True}


@router.delete("/api/conversations/{conv_id}")
async def api_conversation_delete(conv_id: int):
    """Supprime une conversation et tous ses messages."""
    if not delete_conversation(conv_id):
        raise HTTPException(404, "Conversation non trouvée")
    return {"ok": True}


@router.post("/api/conversations/{conv_id}/archive")
async def api_conversation_archive(conv_id: int):
    """Archive une conversation."""
    if not update_conversation(conv_id, archived=True):
        raise HTTPException(404, "Conversation non trouvée")
    return {"ok": True}


@router.post("/api/conversations/{conv_id}/pin")
async def api_conversation_pin(conv_id: int):
    """Bascule le statut épinglé d'une conversation."""
    conv = get_conversation_detail(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation non trouvée")
    pinned = not bool(conv.get("pinned", False))
    update_conversation(conv_id, pinned=pinned)
    return {"ok": True, "pinned": pinned}


@router.post("/api/conversations/{conv_id}/upload")
async def api_conversation_upload(
    conv_id: int,
    file: UploadFile,
    cloud_consent: bool = Form(False),
):
    """Upload et analyse un document dans le contexte d'une conversation."""
    conv = get_conversation_detail(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation non trouvée")
    try:
        ensure_cloud_summary_allowed(cloud_consent)
    except DocumentCloudBlocked as exc:
        raise HTTPException(409, str(exc)) from exc

    try:
        stored = await store_upload(
            file,
            namespace=f"conversations/{conv_id}",
            allowed_extensions=CONVERSATION_EXTENSIONS,
        )
    except UploadRejected as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc

    committed = False
    try:
        if stored.extension == ".pdf":
            try:
                with fitz.open(str(stored.path)) as doc:
                    extracted = "\n".join(page.get_text() for page in doc)
            except Exception as exc:
                raise HTTPException(415, "Document PDF illisible") from exc
        else:
            extracted = stored.path.read_text(encoding="utf-8")

        try:
            summary_result = await summarize_document(
                extracted,
                cloud_consent=cloud_consent,
            )
        except DocumentCloudBlocked as exc:
            raise HTTPException(409, str(exc)) from exc
        summary = summary_result.summary

        try:
            doc_id = save_conversation_document(
                conv_id,
                stored.stored_name,
                stored.original_name,
                str(stored.path),
                stored.extension.lstrip("."),
                stored.size,
                extracted or None,
                summary,
                cloud_consent=cloud_consent,
            )
        except Exception as exc:
            logger.exception("[conv upload] enregistrement DB impossible")
            raise HTTPException(500, "Enregistrement du document impossible") from exc
        committed = True
    except BaseException:
        if not committed:
            remove_managed_upload(stored.path)
        raise

    if stored.extension == ".pdf" and extracted:
        try:
            save_school_document(
                title=Path(stored.original_name).stem,
                content=extracted,
                doc_type="cours",
                file_path=str(stored.path),
            )
        except Exception as e:
            logger.debug("[conv upload] school_doc : %s", e)

    logger.info(
        "[conv upload] doc #%d dans conv #%d (%s, %d bytes)",
        doc_id,
        conv_id,
        stored.original_name,
        stored.size,
    )
    return {
        "ok": True,
        "doc_id": doc_id,
        "filename": stored.original_name,
        "file_type": stored.extension.lstrip("."),
        "size": stored.size,
        "content_length": len(extracted),
        **summary_result.as_dict(),
    }

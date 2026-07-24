"""Routes des conversations et de leurs documents."""

from __future__ import annotations

import logging
from pathlib import Path

import fitz
from fastapi import APIRouter, Body, HTTPException, UploadFile
from fastapi.responses import JSONResponse

import config
import llm
from database import (
    delete_conversation,
    get_conversation_detail,
    get_conversations,
    save_conversation_document,
    save_school_document,
    search_conversations,
    update_conversation,
)

router = APIRouter()
logger = logging.getLogger("jarvis")


# ── Conversations enrichies ──────────────────────────────────


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
async def api_conversation_upload(conv_id: int, file: UploadFile):
    """Upload et analyse un document dans le contexte d'une conversation."""
    import time as _time

    conv = get_conversation_detail(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation non trouvée")

    upload_dir = Path(config.UPLOAD_DIR) / "conversations" / str(conv_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{int(_time.time())}_{file.filename}"
    filepath = upload_dir / filename
    content_bytes = await file.read()
    filepath.write_bytes(content_bytes)

    ext = Path(file.filename or "").suffix.lower()
    extracted = ""

    if ext == ".pdf":
        try:
            doc = fitz.open(str(filepath))
            extracted = "\n".join(page.get_text() for page in doc)
            doc.close()
        except Exception as e:
            logger.warning("[conv upload] PDF extraction : %s", e)
    elif ext in (".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".css"):
        try:
            extracted = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning("[conv upload] text read : %s", e)

    summary = None
    if len(extracted) > 500:
        try:
            res = await llm.chat(
                messages=[{"role": "user", "content": extracted[:5000]}],
                model=config.DEEPSEEK_FAST_MODEL,
                system="Résume ce document en 2-3 phrases. Sois factuel.",
                max_tokens=150,
                use_cache=False,
            )
            summary = (res.get("content") or "").strip()
        except Exception as e:
            logger.warning("[conv upload] résumé Haiku : %s", e)

    doc_id = save_conversation_document(
        conv_id,
        filename,
        file.filename or filename,
        str(filepath),
        ext.lstrip(".") or "bin",
        len(content_bytes),
        extracted or None,
        summary,
    )

    if ext == ".pdf" and extracted:
        try:
            save_school_document(
                title=Path(file.filename or filename).stem,
                content=extracted,
                doc_type="cours",
                file_path=str(filepath),
            )
        except Exception as e:
            logger.debug("[conv upload] school_doc : %s", e)

    logger.info("[conv upload] doc #%d dans conv #%d (%s, %d bytes)", doc_id, conv_id, file.filename, len(content_bytes))
    return {
        "ok": True,
        "doc_id": doc_id,
        "filename": file.filename,
        "file_type": ext.lstrip("."),
        "size": len(content_bytes),
        "content_length": len(extracted),
        "summary": summary,
    }

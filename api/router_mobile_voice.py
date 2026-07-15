"""Endpoint HTTP push-to-talk pour le compagnon Android."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from api.mobile_voice_service import MobileVoiceError, process_mobile_voice_turn
from api.router_auth import _require_mobile_device

logger = logging.getLogger("jarvis.mobile_voice")

router = APIRouter()


@router.post("/api/mobile/voice/turn")
async def api_mobile_voice_turn(
    request: Request,
    audio: UploadFile = File(...),
    conversation_id: int | None = Form(None),
) -> dict:
    """Tour vocal push-to-talk : audio entrant → STT → JARVIS → TTS → JSON."""
    device = _require_mobile_device(request)
    raw = await audio.read()
    try:
        return await process_mobile_voice_turn(
            device,
            raw,
            conversation_id=conversation_id,
        )
    except MobileVoiceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except Exception as exc:
        logger.exception("[mobile_voice] erreur inattendue device=%s", device.get("device_id"))
        raise HTTPException(500, "Erreur interne du tour vocal") from exc

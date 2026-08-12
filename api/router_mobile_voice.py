"""Endpoint HTTP push-to-talk pour le compagnon Android."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

import config
from api.mobile_voice_service import MobileVoiceError, process_mobile_voice_turn
from api.router_auth import _require_mobile_device
from jarvis.uploads import UploadRejected, read_upload_limited

logger = logging.getLogger("jarvis.mobile_voice")

router = APIRouter()


@router.post("/api/mobile/voice/turn")
async def api_mobile_voice_turn(
    request: Request,
    audio: UploadFile = File(...),
    conversation_id: int | None = Form(None),
    locale: str | None = Form(None),
    timezone: str | None = Form(None),
) -> dict:
    """Tour vocal push-to-talk : audio entrant → STT → JARVIS → TTS → JSON."""
    device = _require_mobile_device(request)
    try:
        raw = await read_upload_limited(audio, max_bytes=config.MOBILE_VOICE_MAX_BYTES)
        return await process_mobile_voice_turn(
            device,
            raw,
            conversation_id=conversation_id,
            locale=locale,
            timezone_name=timezone,
        )
    except UploadRejected as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    except MobileVoiceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except Exception as exc:
        logger.exception("[mobile_voice] erreur inattendue device=%s", device.get("device_id"))
        raise HTTPException(500, "Erreur interne du tour vocal") from exc

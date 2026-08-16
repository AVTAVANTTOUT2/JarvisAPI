"""Traitement borné des paquets audio WebSocket."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket

from api.ws_recordings import WebSocketRecordingController

logger = logging.getLogger("jarvis")


async def handle_ws_audio_packet(
    ws: WebSocket,
    audio_bytes: bytes,
    *,
    recording: WebSocketRecordingController,
    conv_session: dict[str, Any] | None,
    is_speaking: bool,
    conversation_mode: bool,
    conv_audio_buffer: list[bytes],
    conversation_id: int,
    confirmation_session_id: str,
    agentic_context: Any,
    stt: Any,
    language: str,
    hands_free_fn: Callable[..., Any],
    process_message_fn: Callable[..., Any],
) -> bool:
    """Consomme un blob audio et retourne le nouvel état de lecture TTS."""

    if recording.add_audio(audio_bytes):
        return is_speaking

    if conv_session and conv_session.get("active"):
        if conv_session.get("is_processing") and not conv_session.get("is_speaking"):
            return is_speaking
        await hands_free_fn(ws, audio_bytes, conv_session)
        return is_speaking

    if is_speaking:
        return is_speaking
    if conversation_mode:
        conv_audio_buffer.append(audio_bytes)
        return is_speaking

    logger.info("Audio poussoir reçu (%d octets)", len(audio_bytes))
    if stt is None or not getattr(stt, "available", False):
        await ws.send_json(
            {
                "type": "error",
                "message": "STT local indisponible (moteur ou modèle absent).",
            }
        )
        return is_speaking

    await ws.send_json({"type": "status", "content": "Transcription en cours…"})
    try:
        text = await stt.transcribe(audio_bytes, language=language)
    except Exception as exc:
        logger.exception("Erreur STT : %s", exc)
        await ws.send_json(
            {
                "type": "error",
                "message": f"Erreur transcription : {type(exc).__name__}",
            }
        )
        return is_speaking

    if not text or len(text) < 2:
        await ws.send_json(
            {"type": "error", "message": "Je n'ai pas compris, réessaie."}
        )
        return is_speaking

    await ws.send_json({"type": "transcript", "content": text})
    try:
        await process_message_fn(
            ws,
            text,
            conversation_id,
            voice_mode=True,
            stream=True,
            send_tts=True,
            confirmation_session_id=confirmation_session_id,
            agentic_context=agentic_context.agentic_kwargs(),
        )
    except Exception as exc:
        logger.exception("Erreur traitement message audio")
        await ws.send_json(
            {
                "type": "error",
                "message": f"Erreur agent : {type(exc).__name__}",
            }
        )
        return is_speaking
    return True

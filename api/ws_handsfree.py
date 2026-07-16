"""Traitement d'un blob audio en mode conversation mains libres."""

from __future__ import annotations

import logging

from fastapi import WebSocket

import config
from agents.display_text import finalize_assistant_display_text
from api.chat_context import _send_tts_streaming
from api.voice_processing import _process_voice_fast

try:
    from audio import stt
except ImportError:
    stt = None

logger = logging.getLogger("jarvis")


async def _handle_hands_free_blob(
    ws: WebSocket, audio_bytes: bytes, conv_session: dict,
) -> None:
    """Mains libres : STT → pipeline vocal rapide (`_process_voice_fast`) + TTS."""
    cid = conv_session["conversation_id"]
    conv_session["is_processing"] = True

    async def reset_listening(send_processing_done: bool = True):
        conv_session["is_processing"] = False
        conv_session["is_speaking"] = False
        if conv_session.get("active") and send_processing_done:
            await ws.send_json({"type": "listening"})

    try:
        import time as _time

        await ws.send_json({"type": "processing"})

        if stt is None or not getattr(stt, "available", False):
            await ws.send_json({"type": "error", "message": "STT local indisponible (moteur ou modèle absent)."})
            await reset_listening()
            return

        if len(audio_bytes) < 1000:
            await reset_listening()
            return

        _t_stt = _time.time()
        try:
            text = await stt.transcribe(audio_bytes, language=config.LANGUAGE)
        except Exception as e:
            logger.exception("STT mains libres : %s", e)
            await ws.send_json({"type": "error", "message": f"Transcription : {type(e).__name__}"})
            await reset_listening()
            return
        stt_ms = round((_time.time() - _t_stt) * 1000)

        await ws.send_json({
            "type": "voice_debug",
            "blob_bytes": len(audio_bytes),
            "stt_raw": getattr(stt, "last_raw_text", "")[:220],
            "stt_clean": (text or "")[:220],
        })

        if not text or len(text.strip()) < 2:
            await reset_listening()
            return

        await ws.send_json({"type": "transcript", "content": text})

        conv_session["is_processing"] = False
        conv_session["is_speaking"] = True

        try:
            result = await _process_voice_fast(text, cid, stt_ms=stt_ms)
            display_text = finalize_assistant_display_text(result.get("text", ""))
            emotion = result.get("emotion", "neutral") or "neutral"
            await ws.send_json({
                "type": "response",
                "agent": "voice",
                "category": "VOICE",
                "content": display_text,
                "model": config.DEEPSEEK_FAST_MODEL,
                "tokens_in": 0,
                "tokens_out": 0,
                "cost": float(result.get("cost") or 0.0),
                "emotion": emotion,
            })
            _t_tts = _time.time()
            await _send_tts_streaming(ws, display_text, emotion)
            # Complète la trace persistée avec la latence TTS réelle
            trace_id = result.get("trace_id")
            if trace_id:
                try:
                    from database import update_voice_debug_latency

                    tts_ms = round((_time.time() - _t_tts) * 1000)
                    update_voice_debug_latency(
                        int(trace_id),
                        tts_ms=tts_ms,
                        total_ms=int(result.get("latency_ms") or 0) + tts_ms,
                    )
                except Exception as e:
                    logger.debug("[hands_free] update tts latency : %s", e)
        except Exception as e:
            logger.exception("traitement message mains libres : %s", e)
            await ws.send_json({"type": "error", "message": f"Erreur agent : {type(e).__name__}"})
            conv_session["is_speaking"] = False
            await reset_listening()
            return

    except Exception as e:
        logger.exception("hands_free pipeline : %s", e)
        await reset_listening()

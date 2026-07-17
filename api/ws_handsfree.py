"""Traitement d'un blob audio en mode conversation mains libres."""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import WebSocket

import config
from agents.display_text import finalize_assistant_display_text
from api.chat_context import _send_tts_streaming
from api.voice_processing import _match_voice_control, _process_voice_fast

try:
    from audio import stt
except ImportError:
    stt = None

logger = logging.getLogger("jarvis")


def _new_turn_id() -> str:
    return f"turn-{uuid.uuid4().hex[:12]}"


async def cancel_current_voice_turn(conv_session: dict) -> None:
    """Annule le tour TTS en cours (barge-in)."""
    event = conv_session.get("cancel_event")
    if event is not None and not event.is_set():
        event.set()
    task = conv_session.get("tts_task")
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    conv_session["is_speaking"] = False
    conv_session["cancelled_turn_id"] = conv_session.get("turn_id")


async def handle_voice_cancel_message(ws: WebSocket, conv_session: dict | None) -> None:
    """Traite le message WS ``voice_cancel`` (PTT / bouton)."""
    if not conv_session or not conv_session.get("active"):
        return
    await cancel_current_voice_turn(conv_session)
    await ws.send_json({
        "type": "speech_cancelled",
        "turn_id": conv_session.get("cancelled_turn_id"),
    })
    await ws.send_json({"type": "listening"})


async def _handle_barge_in_blob(
    ws: WebSocket, audio_bytes: bytes, conv_session: dict,
) -> bool:
    """Pendant is_speaking : STT court → commande contrôle → cancel TTS.

    Politique Option A (commande uniquement) : seules les commandes courtes
    reconnues par ``_match_voice_control`` interrompent le TTS ; toute autre
    parole est ignorée pour éviter les faux positifs et les tours parallèles.

    Retourne True si le barge-in a été traité (commande reconnue).
    """
    if stt is None or not getattr(stt, "available", False):
        return False
    if len(audio_bytes) < 800:
        return False
    try:
        text = await stt.transcribe(audio_bytes, language=config.LANGUAGE)
    except Exception as exc:
        logger.debug("[barge_in] STT : %s", exc)
        return False
    control = _match_voice_control(text or "")
    if control is None:
        # Pas une commande — on ignore (évite de lancer un second tour pendant TTS)
        return False

    await cancel_current_voice_turn(conv_session)
    await ws.send_json({"type": "transcript", "content": text})
    await ws.send_json({
        "type": "response",
        "agent": "voice",
        "category": "VOICE_CONTROL",
        "content": control,
        "emotion": "neutral",
    })
    # Ack court sans bloquer longtemps
    turn_id = _new_turn_id()
    cancel_event = asyncio.Event()
    conv_session["turn_id"] = turn_id
    conv_session["cancel_event"] = cancel_event
    conv_session["is_speaking"] = True
    try:
        await _send_tts_streaming(
            ws, control, "neutral", turn_id=turn_id, cancel_event=cancel_event
        )
    finally:
        conv_session["is_speaking"] = False
        await ws.send_json({"type": "listening"})
    return True


async def _handle_hands_free_blob(
    ws: WebSocket, audio_bytes: bytes, conv_session: dict,
) -> None:
    """Mains libres : STT → pipeline vocal rapide (`_process_voice_fast`) + TTS."""
    # Barge-in pendant parole
    if conv_session.get("is_speaking"):
        await _handle_barge_in_blob(ws, audio_bytes, conv_session)
        return

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
        turn_id = _new_turn_id()
        cancel_event = asyncio.Event()
        conv_session["turn_id"] = turn_id
        conv_session["cancel_event"] = cancel_event
        conv_session["is_speaking"] = True

        try:
            result = await _process_voice_fast(text, cid, stt_ms=stt_ms)
            if cancel_event.is_set():
                await reset_listening()
                return
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
                "turn_id": turn_id,
            })
            _t_tts = _time.time()
            tts_task = asyncio.create_task(
                _send_tts_streaming(
                    ws, display_text, emotion,
                    turn_id=turn_id, cancel_event=cancel_event,
                ),
                name=f"tts-{turn_id}",
            )
            conv_session["tts_task"] = tts_task
            try:
                status = await tts_task
            except asyncio.CancelledError:
                status = "cancelled"
            # Complète la trace persistée avec la latence TTS réelle
            trace_id = result.get("trace_id")
            if trace_id and status == "completed":
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
            if status == "cancelled":
                conv_session["is_speaking"] = False
                await ws.send_json({"type": "listening"})
                return
        except Exception as e:
            logger.exception("traitement message mains libres : %s", e)
            await ws.send_json({"type": "error", "message": f"Erreur agent : {type(e).__name__}"})
            conv_session["is_speaking"] = False
            await reset_listening()
            return

    except Exception as e:
        logger.exception("hands_free pipeline : %s", e)
        await reset_listening()

"""Pipeline push-to-talk Android : STT local, JARVIS (voix rapide Flash), TTS local."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any

import config
from api.voice_processing import _process_voice_fast
from audio.audio_format import detect_upload_format, is_encoded_audio_container, tts_audio_mime
from audio.stt_daemon import is_stt_prompt_echo, stt_local
from audio.tts import get_tts_by_name, resolve_tts_engine_name, resolve_tts_voice
from database import create_conversation, get_conversation_detail, touch_mobile_device

logger = logging.getLogger("jarvis.mobile_voice")

_device_locks: dict[str, asyncio.Lock] = {}


class MobileVoiceError(Exception):
    """Erreur métier du tour vocal mobile (message utilisateur, code HTTP)."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _device_lock(device_id: str) -> asyncio.Lock:
    lock = _device_locks.get(device_id)
    if lock is None:
        lock = asyncio.Lock()
        _device_locks[device_id] = lock
    return lock


def _detect_container(audio_bytes: bytes) -> tuple[str, str]:
    """Accepte uniquement les vrais conteneurs (M4A/WAV/WebM/MP3/OGG), pas le fallback PCM."""
    if not is_encoded_audio_container(audio_bytes):
        return "", ""
    return detect_upload_format(audio_bytes)


def _validate_audio_payload(audio_bytes: bytes) -> tuple[str, str]:
    size = len(audio_bytes)
    if size < config.MOBILE_VOICE_MIN_BYTES:
        raise MobileVoiceError("Enregistrement trop court ou vide", 400)
    if size > config.MOBILE_VOICE_MAX_BYTES:
        raise MobileVoiceError("Fichier audio trop volumineux", 413)
    filename, mime = _detect_container(audio_bytes)
    if not filename:
        raise MobileVoiceError("Format audio non pris en charge", 415)
    return filename, mime


def _resolve_conversation_id(conversation_id: int | None) -> int:
    if conversation_id is not None:
        try:
            detail = get_conversation_detail(int(conversation_id))
        except (TypeError, ValueError):
            detail = None
        if detail:
            return int(conversation_id)
    return create_conversation(agent="android_voice")


def _stt_engine_label() -> str:
    engine = (getattr(config, "AUDIO_DAEMON_STT_ENGINE", "") or "local").strip().lower()
    if engine in ("local", "faster-whisper"):
        return "faster-whisper"
    return engine or "disabled"


def _stt_model_label() -> str:
    return (getattr(config, "AUDIO_DAEMON_STT_MODEL", "") or "small").strip()


async def process_mobile_voice_turn(
    device: dict[str, Any],
    audio_bytes: bytes,
    *,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    """Exécute un tour vocal complet pour un appareil mobile appairé."""
    device_id = str(device.get("device_id") or "")
    if not device_id:
        raise MobileVoiceError("Appareil inconnu", 401)

    lock = _device_lock(device_id)
    if lock.locked():
        raise MobileVoiceError("Un tour vocal est déjà en cours sur cet appareil", 429)

    async with lock:
        touch_mobile_device(device_id)
        _validate_audio_payload(audio_bytes)

        if not stt_local.available:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, stt_local.preload_sync)
        if not stt_local.available:
            raise MobileVoiceError(
                "Transcription locale indisponible — vérifiez le modèle Whisper sur le Mac",
                503,
            )

        conv_id = _resolve_conversation_id(conversation_id)

        _t_stt = time.time()
        try:
            transcript = await asyncio.wait_for(
                stt_local.transcribe(audio_bytes, language=config.LANGUAGE or "fr"),
                timeout=config.MOBILE_VOICE_STT_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError as exc:
            raise MobileVoiceError("Transcription expirée", 504) from exc
        stt_ms = round((time.time() - _t_stt) * 1000)

        transcript = (transcript or "").strip()
        if not transcript:
            raise MobileVoiceError("Aucune parole détectée dans l'enregistrement", 400)
        if is_stt_prompt_echo(transcript):
            logger.warning(
                "[mobile_voice] STT prompt-echo rejeté device=%s transcript=%r",
                device_id,
                transcript[:120],
            )
            raise MobileVoiceError(
                "Je n'ai pas bien entendu, Monsieur. Réessayez en maintenant le micro "
                "un peu plus longtemps.",
                400,
            )

        logger.info(
            "[mobile_voice] device=%s conv=%s transcript_len=%d transcript=%r",
            device_id,
            conv_id,
            len(transcript),
            transcript[:160],
        )

        # Pipeline vocal unifié : même chemin Flash que la voix desktop
        # (routage cognitif, ack immédiat, actions, délégation Cursor).
        try:
            llm_result = await asyncio.wait_for(
                _process_voice_fast(transcript, conv_id, stt_ms=stt_ms),
                timeout=config.MOBILE_VOICE_LLM_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError as exc:
            raise MobileVoiceError("Traitement JARVIS expiré", 504) from exc

        response_text = str(llm_result.get("text") or "").strip()
        emotion = str(llm_result.get("emotion") or "neutral")

        tts_engine_name = resolve_tts_engine_name()
        tts_engine = get_tts_by_name(tts_engine_name)
        backend = getattr(tts_engine, "get_backend_name", lambda: tts_engine_name)()
        if tts_engine_name == "edge" and not getattr(tts_engine, "available", False):
            raise MobileVoiceError(
                "TTS Edge indisponible — pip install edge-tts (voix française Henri)",
                503,
            )

        audio_mime = tts_audio_mime(backend)
        audio_bytes_out = b""
        tts_error: str | None = None

        if response_text:
            _t_tts = time.time()
            try:
                audio_bytes_out = await asyncio.wait_for(
                    tts_engine.synthesize(response_text, emotion=emotion),
                    timeout=config.MOBILE_VOICE_TTS_TIMEOUT_SEC,
                )
                trace_id = llm_result.get("trace_id")
                if trace_id:
                    try:
                        from database import update_voice_debug_latency

                        tts_ms = round((time.time() - _t_tts) * 1000)
                        update_voice_debug_latency(
                            int(trace_id),
                            tts_ms=tts_ms,
                            total_ms=int(llm_result.get("latency_ms") or 0) + tts_ms,
                        )
                    except Exception as exc:
                        logger.debug("[mobile_voice] update tts latency : %s", exc)
            except asyncio.TimeoutError:
                tts_error = "Synthèse vocale expirée"
                logger.warning("[mobile_voice] TTS timeout device=%s", device_id)
            except Exception as exc:
                tts_error = "Synthèse vocale indisponible"
                logger.warning("[mobile_voice] TTS error device=%s: %s", device_id, exc)
        else:
            response_text = "Je n'ai pas de réponse pour le moment, Monsieur."

        payload: dict[str, Any] = {
            "conversation_id": conv_id,
            "transcript": transcript,
            "response_text": response_text,
            "audio_mime_type": audio_mime if audio_bytes_out else None,
            "audio_base64": base64.b64encode(audio_bytes_out).decode("ascii") if audio_bytes_out else None,
            "audio_url": None,
            "stt_engine": _stt_engine_label(),
            "stt_model": _stt_model_label(),
            "tts_engine": backend,
            "tts_voice": resolve_tts_voice(tts_engine_name),
            "source": "android_voice",
            "device_id": device_id,
            "agent": "voice",
            "emotion": emotion,
        }
        if tts_error:
            payload["tts_error"] = tts_error
        return payload

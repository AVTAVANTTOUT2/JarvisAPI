"""Pipeline push-to-talk Android : STT local, JARVIS, TTS local."""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import config
from api.chat_processing import _process_message_internal
from audio.audio_format import tts_audio_mime
from audio.stt_daemon import stt_local
from audio.tts import get_tts_by_name
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
    if len(audio_bytes) >= 8 and audio_bytes[4:8] == b"ftyp":
        return "audio.m4a", "audio/mp4"
    if len(audio_bytes) >= 4 and audio_bytes[:4] == b"RIFF":
        return "audio.wav", "audio/wav"
    if len(audio_bytes) >= 4 and audio_bytes[:4] == b"\x1a\x45\xdf\xa3":
        return "audio.webm", "audio/webm"
    if len(audio_bytes) >= 3 and audio_bytes[:3] == b"ID3":
        return "audio.mp3", "audio/mpeg"
    if len(audio_bytes) >= 2 and audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xE0) == 0xE0:
        return "audio.mp3", "audio/mpeg"
    if len(audio_bytes) >= 4 and audio_bytes[:4] == b"OggS":
        return "audio.ogg", "audio/ogg"
    return "", ""


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

        try:
            transcript = await asyncio.wait_for(
                stt_local.transcribe(audio_bytes, language=config.LANGUAGE or "fr"),
                timeout=config.MOBILE_VOICE_STT_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError as exc:
            raise MobileVoiceError("Transcription expirée", 504) from exc

        transcript = (transcript or "").strip()
        if not transcript:
            raise MobileVoiceError("Aucune parole détectée dans l'enregistrement", 400)

        logger.info(
            "[mobile_voice] device=%s conv=%s transcript_len=%d",
            device_id,
            conv_id,
            len(transcript),
        )

        try:
            llm_result = await asyncio.wait_for(
                _process_message_internal(transcript, conv_id, voice_mode=True),
                timeout=config.MOBILE_VOICE_LLM_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError as exc:
            raise MobileVoiceError("Traitement JARVIS expiré", 504) from exc

        response_text = str(llm_result.get("text") or "").strip()
        emotion = str(llm_result.get("emotion") or "neutral")

        tts_engine_name = (
            getattr(config, "TTS_ENGINE", "") or config.DEFAULT_TTS_ENGINE
        ).strip().lower()
        tts_engine = get_tts_by_name(tts_engine_name)

        audio_mime = tts_audio_mime(getattr(tts_engine, "get_backend_name", lambda: tts_engine_name)())
        audio_bytes_out = b""
        tts_error: str | None = None

        if response_text:
            try:
                audio_bytes_out = await asyncio.wait_for(
                    tts_engine.synthesize(response_text, emotion=emotion),
                    timeout=config.MOBILE_VOICE_TTS_TIMEOUT_SEC,
                )
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
            "tts_engine": getattr(tts_engine, "get_backend_name", lambda: tts_engine_name)(),
            "tts_voice": getattr(config, "KOKORO_VOICE", "") if tts_engine_name == "kokoro" else None,
            "source": "android_voice",
            "device_id": device_id,
            "agent": llm_result.get("agent"),
            "emotion": emotion,
        }
        if tts_error:
            payload["tts_error"] = tts_error
        return payload

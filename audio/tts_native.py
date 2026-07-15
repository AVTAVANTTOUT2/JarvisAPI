"""Moteurs TTS locaux pour le daemon audio — aucun Edge/ElevenLabs."""

from __future__ import annotations

import asyncio
import io
import logging
import struct
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import config
from jarvis.event_bus import JarvisEvent, event_bus

logger = logging.getLogger(__name__)

FRENCH_KOKORO_VOICE = "ff_siwis"


class TTSKitEngine:
    """TTSKit + Qwen3-TTS 0.6B (local) — chargement explicite, pas de téléchargement auto."""

    SAMPLE_RATE = 24000

    def __init__(self) -> None:
        self._model: Any = None
        self._load_failed = False
        self._model_name = getattr(config, "TTS_MODEL", "qwen3-tts-0.6b")
        self._language = getattr(config, "TTS_LANGUAGE", "fr")
        self.available = False

    def preload_sync(self) -> bool:
        if self._model is not None:
            return True
        if self._load_failed:
            return False
        try:
            # Interface volontairement optionnelle — le package peut ne pas exister.
            from ttskit import TTSKit  # type: ignore[import-not-found]

            model_path = Path(getattr(config, "TTS_MODEL_PATH", "") or "").expanduser()
            if model_path.is_dir():
                self._model = TTSKit(model_path=str(model_path), language=self._language)
            else:
                self._model = TTSKit(model=self._model_name, language=self._language)
            self.available = True
            logger.info("[TTS] TTSKit chargé (%s, lang=%s)", self._model_name, self._language)
            return True
        except ImportError:
            logger.warning("[TTS] TTSKit absent — pip install ttskit (optionnel)")
        except Exception as e:
            logger.warning("[TTS] TTSKit indisponible : %s", e)
        self._load_failed = True
        self.available = False
        return False

    def get_backend_name(self) -> str:
        return "ttskit"

    async def synthesize(self, text: str, emotion: str = "neutral") -> bytes:
        chunks = [c async for c in self.synthesize_stream(text, emotion)]
        return b"".join(chunks)

    async def synthesize_stream(
        self, text: str, emotion: str = "neutral",
    ) -> AsyncGenerator[bytes, None]:
        if not text.strip():
            return
        if not self.preload_sync() or self._model is None:
            return

        asyncio.create_task(event_bus.emit(JarvisEvent(
            type="tts.start", data={"engine": "ttskit", "text_length": len(text)},
        )))

        loop = asyncio.get_running_loop()

        def _stream_pcm() -> list[bytes]:
            out: list[bytes] = []
            stream_fn = getattr(self._model, "synthesize_stream", None)
            if callable(stream_fn):
                for chunk in stream_fn(text, language=self._language):
                    pcm = _to_pcm16_bytes(chunk)
                    if pcm:
                        out.append(pcm)
            else:
                full = self._model.synthesize(text, language=self._language)
                pcm = _to_pcm16_bytes(full)
                if pcm:
                    out.append(pcm)
            return out

        try:
            for chunk in await loop.run_in_executor(None, _stream_pcm):
                yield chunk
        finally:
            asyncio.create_task(event_bus.emit(JarvisEvent(type="tts.done")))


def _to_pcm16_bytes(chunk: Any) -> bytes:
    """Normalise ndarray / bytes / float32 en PCM16."""
    try:
        import numpy as np  # type: ignore[import-untyped]

        if isinstance(chunk, (bytes, bytearray)):
            return bytes(chunk)
        arr = np.asarray(chunk, dtype=np.float32)
        if arr.size == 0:
            return b""
        arr = np.clip(arr, -1.0, 1.0)
        pcm = (arr * 32767.0).astype(np.int16)
        return pcm.tobytes()
    except Exception:
        return b""


ttskit_tts = TTSKitEngine()


def get_native_tts_engine() -> Any:
    """Chaîne locale : TTSKit → Kokoro (voix FR) → macOS say. Jamais Edge/ElevenLabs."""
    from audio.tts import kokoro_tts, macos_tts

    pref = (getattr(config, "TTS_ENGINE", "ttskit") or "ttskit").lower().strip()
    if pref == "ttskit" and ttskit_tts.preload_sync():
        return ttskit_tts
    if pref == "kokoro" and kokoro_tts.available:
        return kokoro_tts
    if pref == "macos" and macos_tts.available:
        return macos_tts
    # Repli local uniquement
    if ttskit_tts.available:
        return ttskit_tts
    if kokoro_tts.available:
        logger.info("[TTS native] Repli Kokoro (voix %s)", FRENCH_KOKORO_VOICE)
        return kokoro_tts
    if macos_tts.available:
        logger.info("[TTS native] Repli macOS say")
        return macos_tts
    logger.error("[TTS native] Aucun moteur local disponible")
    return None


def native_tts_sample_rate(engine: Any) -> int:
    if engine is None:
        return 24000
    name = getattr(engine, "get_backend_name", lambda: "")()
    if name == "ttskit":
        return TTSKitEngine.SAMPLE_RATE
    if name == "kokoro":
        return 24000
    return 44100


__all__ = [
    "FRENCH_KOKORO_VOICE",
    "TTSKitEngine",
    "get_native_tts_engine",
    "native_tts_sample_rate",
    "ttskit_tts",
]

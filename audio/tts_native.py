"""Moteurs TTS locaux pour le daemon audio — aucun service TTS réseau."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

import config
from audio.engine_config import SETUP_SCRIPT
from jarvis.event_bus import JarvisEvent, event_bus

logger = logging.getLogger(__name__)


class TTSKitEngine:
    """TTSKit + Qwen3-TTS 0.6B (local) — chargement explicite, pas de téléchargement auto."""

    SAMPLE_RATE = 24000

    def __init__(self) -> None:
        self._model_name = getattr(config, "TTS_MODEL", "qwen3-tts-0.6b")
        self._language = getattr(config, "TTS_LANGUAGE", "fr")
        self._model_path = str(getattr(config, "TTS_MODEL_PATH", "") or "")
        self.available = False

    def preload_sync(self) -> bool:
        try:
            from native_audio.ttskit_bridge import is_ttskit_available

            self.available = is_ttskit_available()
        except Exception as e:
            logger.debug("[TTS] Vérification sidecar TTSKit : %s", e)
            self.available = False
        if self.available:
            logger.info("[TTS] Sidecar TTSKit prêt (%s, lang=%s)", self._model_name, self._language)
        else:
            logger.warning("[TTS] Sidecar TTSKit absent — voir native_audio/README.md")
        return self.available

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
        if not self.preload_sync():
            return

        asyncio.create_task(event_bus.emit(JarvisEvent(
            type="tts.start", data={"engine": "ttskit", "text_length": len(text)},
        )))

        try:
            from native_audio.ttskit_bridge import stream_pcm16

            async for chunk in stream_pcm16(
                text,
                model=self._model_name,
                language=self._language,
                model_path=self._model_path,
            ):
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


def get_native_tts_engine(*, exclude: frozenset[str] = frozenset()) -> Any:
    """Chaîne locale : moteur configuré → Kokoro → macOS → TTSKit."""
    from audio.tts import kokoro_tts, macos_tts

    pref = (
        getattr(config, "TTS_ENGINE", config.DEFAULT_TTS_ENGINE) or config.DEFAULT_TTS_ENGINE
    ).lower().strip()
    if pref == "kokoro" and "kokoro" not in exclude and kokoro_tts.available:
        return kokoro_tts
    if pref == "ttskit" and "ttskit" not in exclude and ttskit_tts.preload_sync():
        return ttskit_tts
    if pref == "macos" and "macos" not in exclude and macos_tts.available:
        return macos_tts
    if pref == "kokoro" and "kokoro" not in exclude:
        logger.warning("[TTS native] Kokoro indisponible — repli local (voir %s)", SETUP_SCRIPT)
    if "kokoro" not in exclude and kokoro_tts.available:
        logger.info(
            "[TTS native] Repli Kokoro (voix %s)",
            getattr(config, "KOKORO_VOICE", config.DEFAULT_KOKORO_VOICE),
        )
        return kokoro_tts
    if "macos" not in exclude and macos_tts.available:
        logger.info("[TTS native] Repli macOS say")
        return macos_tts
    if "ttskit" not in exclude and ttskit_tts.preload_sync():
        return ttskit_tts
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
    "TTSKitEngine",
    "get_native_tts_engine",
    "native_tts_sample_rate",
    "ttskit_tts",
]

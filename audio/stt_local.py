"""Speech-to-Text local via faster-whisper — zero latence reseau, zero cout API.

Utilise le modele `small` (244 Mo, ~100ms de transcription, meilleur compromis
precision/latence pour le francais) ou `base` (142 Mo), `tiny` (75 Mo).
Optimise pour Apple Silicon via CTranslate2 (faster-whisper en interne).

Usage :
    stt_local = LocalSTT()
    text = await stt_local.transcribe(wav_bytes)
    meta = await stt_local.transcribe_with_metadata(wav_bytes)  # avec segments
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

# Modele par defaut — small (244 Mo, bon compromis precision/latence pour le francais)
DEFAULT_MODEL_SIZE = "small"
MODEL_SIZES = frozenset({"tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en", "large-v3"})


class LocalSTT:
    """STT local singleton via faster-whisper + CTranslate2 (Apple Silicon natif)."""

    def __init__(self) -> None:
        self._model: Any = None
        self._model_size: str = ""
        self._loaded: bool = False
        self._load_lock: asyncio.Lock = asyncio.Lock()

        stt_engine = (getattr(config, "AUDIO_DAEMON_STT_ENGINE", "") or "").strip()
        # active par defaut si faster-whisper est installe ET pas explicitement desactive
        self.available: bool = (stt_engine != "cloud")
        self.last_text: str = ""

        if not self.available:
            logger.info("[STT-local] Non active (AUDIO_DAEMON_STT_ENGINE != local)")
            return

        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            logger.error("[STT-local] faster-whisper non installe — pip install faster-whisper")
            self.available = False
            return

        model_size = (getattr(config, "AUDIO_DAEMON_STT_MODEL", "") or "").strip()
        if model_size not in MODEL_SIZES:
            model_size = DEFAULT_MODEL_SIZE

        self._model_size = model_size
        logger.info("[STT-local] Configure : modele=%s", model_size)

    async def _ensure_model(self) -> bool:
        """Charge le modele une seule fois (lazy, thread-safe)."""
        if self._loaded:
            return True

        async with self._load_lock:
            if self._loaded:
                return True

            try:
                from faster_whisper import WhisperModel

                compute_type = "auto"  # float16 sur GPU, int8 sur CPU

                # Numera de threads pour Apple Silicon (performance cores)
                cpu_count = os.cpu_count() or 4
                num_workers = max(1, min(cpu_count // 2, 4))

                model_path_or_size = self._model_size

                logger.info("[STT-local] Chargement du modele %s (compute=%s, workers=%d) ...",
                            model_path_or_size, compute_type, num_workers)

                self._model = WhisperModel(
                    model_path_or_size,
                    device="auto",
                    compute_type=compute_type,
                    num_workers=num_workers,
                    download_root=str(Path.home() / ".cache" / "faster-whisper"),
                )
                self._loaded = True
                logger.info("[STT-local] Modele %s charge", model_path_or_size)
                return True
            except Exception as e:
                logger.exception("[STT-local] Echec chargement du modele : %s", e)
                self.available = False
                return False

    def _ensure_model_sync(self) -> bool:
        """Charge le modele de maniere synchrone (pour pre-chargement au boot dans un executor)."""
        if self._loaded:
            return True
        try:
            from faster_whisper import WhisperModel

            compute_type = "auto"
            cpu_count = os.cpu_count() or 4
            num_workers = max(1, min(cpu_count // 2, 4))
            model_path_or_size = self._model_size

            logger.info("[STT-local] Pre-chargement synchrone %s (compute=%s, workers=%d) ...",
                        model_path_or_size, compute_type, num_workers)

            self._model = WhisperModel(
                model_path_or_size,
                device="auto",
                compute_type=compute_type,
                num_workers=num_workers,
                download_root=str(Path.home() / ".cache" / "faster-whisper"),
            )
            self._loaded = True
            logger.info("[STT-local] Modele %s pre-charge", model_path_or_size)
            return True
        except Exception as e:
            logger.exception("[STT-local] Echec pre-chargement : %s", e)
            self.available = False
            return False

    async def transcribe(self, audio_bytes: bytes, language: str = "fr", timeout: float | None = None) -> str:
        """Transcrit des bytes audio WAV en texte via faster-whisper.

        Version simplifiee qui retourne uniquement le texte.
        Pour les segments avec scores de confiance, utiliser transcribe_with_metadata.
        """
        result = await self.transcribe_with_metadata(audio_bytes, language, timeout)
        return result["text"] if result else ""

    async def transcribe_with_metadata(
        self,
        audio_bytes: bytes,
        language: str = "fr",
        timeout: float | None = None,
    ) -> dict | None:
        """Transcrit + retourne ``{text, segments, language, duration}``.

        ``segments`` contient les objets Segment de faster-whisper avec
        ``avg_logprob`` pour le filtrage de confiance.
        Retourne ``None`` si indisponible ou echec.
        """
        if not self.available:
            return None
        if len(audio_bytes) < 1000:
            return None
        if not await self._ensure_model():
            return None
        if self._model is None:
            return None

        try:
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            try:
                loop = asyncio.get_running_loop()
                segments, info = await loop.run_in_executor(
                    None,
                    lambda: self._model.transcribe(
                        tmp_path,
                        language=language if language != "auto" else None,
                        beam_size=5,
                        vad_filter=False,
                    ),
                )

                segments_list = list(segments)
                texts = [s.text.strip() for s in segments_list if s.text.strip()]
                text = " ".join(texts).strip()
                self.last_text = text
                logger.debug(
                    "[STT-local] Transcription : %s",
                    text[:100] if len(text) > 100 else text,
                )

                return {
                    "text": text,
                    "segments": segments_list,
                    "language": info.language if info else None,
                    "duration": info.duration if info else None,
                }
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            logger.exception("[STT-local] Echec transcription")
            return None

    def get_backend_name(self) -> str:
        return f"local:{self._model_size}"


stt_local = LocalSTT()

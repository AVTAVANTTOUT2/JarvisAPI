"""Speech-to-Text local via faster-whisper — zero latence reseau, zero cout API.

Utilise le modele `tiny` (75 Mo, ~20 ms de transcription par tranche audio)
ou `small` (500 Mo, plus precis). Optimise pour Apple Silicon via CTranslate2
(c'est exactement ce que fait faster-whisper en interne).

Usage :
    stt_local = LocalSTT()
    text = await stt_local.transcribe(wav_bytes)
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

# Modele par defaut — tiny (75 Mo, ultra-rapide) ; small (500 Mo, plus precis)
DEFAULT_MODEL_SIZE = "base"
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

        Args:
            audio_bytes: PCM 16-bit 16kHz mono WAV
            language: code langue (fr, en, ...)
            timeout: ignore (pour compatibilite API avec STT cloud)

        Returns:
            texte transcrit, ou "" si echec
        """
        if not self.available:
            return ""

        if len(audio_bytes) < 1000:
            return ""

        if not await self._ensure_model():
            return ""

        if self._model is None:
            return ""

        try:
            # Sauvegarde temporaire (faster-whisper lit un fichier)
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
                        vad_filter=False,  # pas besoin, on fait deja le VAD cote daemon
                    ),
                )

                texts = []
                for seg in segments:
                    t = seg.text.strip()
                    if t:
                        texts.append(t)

                result = " ".join(texts).strip()
                self.last_text = result
                logger.debug("[STT-local] Transcription : %s", result[:100] if len(result) > 100 else result)
                return result
            finally:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

        except Exception as e:
            logger.exception("[STT-local] Transcription echouee : %s", e)
            return ""

    def get_backend_name(self) -> str:
        return f"local:{self._model_size}"


stt_local = LocalSTT()

"""STT local multi-moteurs pour le daemon audio macOS — aucun repli cloud."""

from __future__ import annotations

import asyncio
import io
import logging
import struct
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config
from audio.resample import resample_pcm16_mono

logger = logging.getLogger(__name__)

STT_ENGINES = frozenset({
    "whisperkit", "whispercpp", "local", "faster-whisper", "none", "disabled",
})
FASTER_WHISPER_SIZES = frozenset({
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v3",
})

DEFAULT_INITIAL_PROMPT = (
    "JARVIS, DeepSeek, Messages, Mail, Calendar, Safari, Terminal, "
    "Visual Studio Code, Blue Snowball"
)


@dataclass
class TranscriptionResult:
    text: str
    segments: list[Any] = field(default_factory=list)
    language: str | None = None
    duration: float | None = None
    is_partial: bool = False
    engine: str = "none"


def build_initial_prompt() -> str:
    extra = getattr(config, "VOICE_STT_KEYTERMS", ()) or ()
    if isinstance(extra, str):
        terms = [t.strip() for t in extra.split(",") if t.strip()]
    else:
        terms = [str(t).strip() for t in extra if str(t).strip()]
    base = DEFAULT_INITIAL_PROMPT
    if terms:
        base = f"{base}, " + ", ".join(terms[:40])
    return base


class DaemonSTTBackend(ABC):
    """Interface commune des moteurs STT locaux."""

    name: str = "base"

    @abstractmethod
    def preload_sync(self) -> bool:
        """Précharge le modèle au démarrage — jamais pendant une conversation."""

    @abstractmethod
    async def transcribe_pcm(
        self,
        pcm_bytes: bytes,
        *,
        sample_rate: int,
        language: str = "fr",
    ) -> TranscriptionResult | None:
        """Transcrit du PCM mono 16-bit (rééchantillonné en interne si besoin)."""


class DisabledSTT(DaemonSTTBackend):
    name = "disabled"

    def preload_sync(self) -> bool:
        return False

    async def transcribe_pcm(
        self, pcm_bytes: bytes, *, sample_rate: int, language: str = "fr",
    ) -> TranscriptionResult | None:
        return None


class FasterWhisperBackend(DaemonSTTBackend):
    name = "faster-whisper"

    def __init__(self, model_size: str) -> None:
        self._model_size = model_size
        self._model: Any = None
        self._loaded = False
        self._load_lock = asyncio.Lock()

    def preload_sync(self) -> bool:
        if self._loaded:
            return True
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            logger.error("[stt_daemon] faster-whisper absent — pip install faster-whisper")
            return False

        import os

        cpu_count = os.cpu_count() or 4
        num_workers = max(1, min(cpu_count // 2, 4))
        try:
            self._model = WhisperModel(
                self._model_size,
                device="auto",
                compute_type="auto",
                num_workers=num_workers,
                download_root=str(Path.home() / ".cache" / "faster-whisper"),
            )
            self._loaded = True
            logger.info("[stt_daemon] faster-whisper prêt (%s)", self._model_size)
            return True
        except Exception as e:
            logger.exception("[stt_daemon] Chargement faster-whisper : %s", e)
            return False

    async def transcribe_pcm(
        self,
        pcm_bytes: bytes,
        *,
        sample_rate: int,
        language: str = "fr",
    ) -> TranscriptionResult | None:
        if not self._loaded and not self.preload_sync():
            return None
        if self._model is None or len(pcm_bytes) < 1000:
            return None

        pcm_16k = resample_pcm16_mono(pcm_bytes, sample_rate, 16000)
        prompt = build_initial_prompt()

        def _run() -> TranscriptionResult | None:
            try:
                segments_iter, info = self._model.transcribe(
                    _pcm_to_float32_ndarray(pcm_16k, 16000),
                    language=language if language != "auto" else None,
                    beam_size=5,
                    vad_filter=False,
                    initial_prompt=prompt,
                )
                segments_list = list(segments_iter)
                text = " ".join(s.text.strip() for s in segments_list if s.text.strip()).strip()
                return TranscriptionResult(
                    text=text,
                    segments=segments_list,
                    language=getattr(info, "language", language),
                    duration=getattr(info, "duration", None),
                    engine=self.name,
                )
            except TypeError:
                # Anciennes versions faster-whisper sans ndarray
                import tempfile

                wav = _pcm16_to_wav(pcm_16k, 16000)
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp.write(wav)
                    path = tmp.name
                try:
                    segments_iter, info = self._model.transcribe(
                        path,
                        language=language if language != "auto" else None,
                        beam_size=5,
                        vad_filter=False,
                        initial_prompt=prompt,
                    )
                    segments_list = list(segments_iter)
                    text = " ".join(s.text.strip() for s in segments_list if s.text.strip()).strip()
                    return TranscriptionResult(
                        text=text,
                        segments=segments_list,
                        language=getattr(info, "language", language),
                        duration=getattr(info, "duration", None),
                        engine=self.name,
                    )
                finally:
                    Path(path).unlink(missing_ok=True)
            except Exception as e:
                logger.exception("[stt_daemon] faster-whisper transcription : %s", e)
                return None

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _run)


class WhisperKitBackend(DaemonSTTBackend):
    name = "whisperkit"

    def __init__(self, model_id: str) -> None:
        self._model_id = model_id
        self._checked = False
        self._available = False

    def preload_sync(self) -> bool:
        if self._checked:
            return self._available
        try:
            from native_audio.whisperkit_bridge import is_whisperkit_available

            self._available = is_whisperkit_available()
        except ImportError:
            self._available = False
        self._checked = True
        if not self._available:
            logger.error(
                "[stt_daemon] WhisperKit indisponible — compilez le sidecar "
                "native_audio/ (voir README)"
            )
        else:
            logger.info("[stt_daemon] WhisperKit sidecar détecté (modèle=%s)", self._model_id)
        return self._available

    async def transcribe_pcm(
        self,
        pcm_bytes: bytes,
        *,
        sample_rate: int,
        language: str = "fr",
    ) -> TranscriptionResult | None:
        if not self.preload_sync():
            return None
        if len(pcm_bytes) < 1000:
            return None

        pcm_16k = resample_pcm16_mono(pcm_bytes, sample_rate, 16000)
        wav = _pcm16_to_wav(pcm_16k, 16000)
        prompt = build_initial_prompt()

        import tempfile

        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(wav)
                tmp_path = tmp.name

            from native_audio.whisperkit_bridge import transcribe_pcm_file

            loop = asyncio.get_running_loop()
            meta = await loop.run_in_executor(
                None,
                lambda: transcribe_pcm_file(
                    Path(tmp_path),
                    model=self._model_id,
                    language=language,
                    initial_prompt=prompt,
                ),
            )
            if not meta:
                return None
            return TranscriptionResult(
                text=str(meta.get("text") or "").strip(),
                segments=meta.get("segments") or [],
                language=meta.get("language") or language,
                engine=self.name,
            )
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)


class WhisperCppBackend(DaemonSTTBackend):
    """Repli whisper.cpp + Metal si binaire ``whisper-cli`` présent."""

    name = "whispercpp"

    def __init__(self, model_path: str) -> None:
        self._model_path = model_path
        self._binary: str | None = None

    def preload_sync(self) -> bool:
        import shutil

        self._binary = shutil.which("whisper-cli") or shutil.which("whisper")
        if not self._binary:
            logger.error("[stt_daemon] whisper.cpp absent — installez whisper-cli dans le PATH")
            return False
        model = Path(self._model_path).expanduser()
        if not model.is_file():
            logger.error("[stt_daemon] Modèle whisper.cpp introuvable : %s", model)
            return False
        logger.info("[stt_daemon] whisper.cpp prêt (%s)", model.name)
        return True

    async def transcribe_pcm(
        self,
        pcm_bytes: bytes,
        *,
        sample_rate: int,
        language: str = "fr",
    ) -> TranscriptionResult | None:
        if not self.preload_sync() or not self._binary:
            return None

        import subprocess
        import tempfile

        pcm_16k = resample_pcm16_mono(pcm_bytes, sample_rate, 16000)
        wav = _pcm16_to_wav(pcm_16k, 16000)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav)
            wav_path = tmp.name

        try:
            cmd = [
                self._binary,
                "-m", str(Path(self._model_path).expanduser()),
                "-f", wav_path,
                "-l", language,
                "--no-timestamps",
            ]
            loop = asyncio.get_running_loop()
            proc = await loop.run_in_executor(
                None,
                lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False),
            )
            if proc.returncode != 0:
                logger.error("[stt_daemon] whisper.cpp code=%s stderr=%s", proc.returncode, proc.stderr[:200])
                return None
            text = (proc.stdout or "").strip()
            return TranscriptionResult(text=text, engine=self.name)
        except Exception as e:
            logger.exception("[stt_daemon] whisper.cpp : %s", e)
            return None
        finally:
            Path(wav_path).unlink(missing_ok=True)


def _pcm16_to_wav(pcm_bytes: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _pcm_to_float32_ndarray(pcm_bytes: bytes, sample_rate: int) -> Any:
    import numpy as np  # type: ignore[import-untyped]

    n = len(pcm_bytes) // 2
    samples = struct.unpack(f"<{n}h", pcm_bytes)
    arr = np.array(samples, dtype=np.float32) / 32768.0
    return arr


def create_daemon_stt_backend() -> DaemonSTTBackend:
    engine = (getattr(config, "AUDIO_DAEMON_STT_ENGINE", "") or "").strip().lower()
    model = (getattr(config, "AUDIO_DAEMON_STT_MODEL", "") or "").strip()

    if engine in ("none", "disabled", ""):
        return DisabledSTT()
    if engine == "whisperkit":
        return WhisperKitBackend(model or "large-v3-v20240930_626MB")
    if engine == "whispercpp":
        return WhisperCppBackend(model or str(Path.home() / "models" / "ggml-large-v3.bin"))
    if engine in ("local", "faster-whisper"):
        size = model if model in FASTER_WHISPER_SIZES or model.startswith("large") else "small"
        return FasterWhisperBackend(size)
    logger.error("[stt_daemon] Moteur STT inconnu %r — désactivé", engine)
    return DisabledSTT()


class DaemonSTT:
    """Façade singleton pour le daemon audio."""

    def __init__(self) -> None:
        self._backend = create_daemon_stt_backend()
        self.available = not isinstance(self._backend, DisabledSTT)

    def preload_sync(self) -> bool:
        return self._backend.preload_sync()

    async def transcribe_with_metadata(
        self,
        pcm_bytes: bytes,
        *,
        sample_rate: int | None = None,
        language: str = "fr",
    ) -> dict | None:
        sr = sample_rate or int(getattr(config, "AUDIO_DAEMON_SAMPLE_RATE", 16000))
        result = await self._backend.transcribe_pcm(pcm_bytes, sample_rate=sr, language=language)
        if result is None:
            return None
        return {
            "text": result.text,
            "segments": result.segments,
            "language": result.language,
            "duration": result.duration,
            "engine": result.engine,
        }

    async def transcribe(self, pcm_bytes: bytes, language: str = "fr") -> str:
        meta = await self.transcribe_with_metadata(pcm_bytes, language=language)
        return (meta or {}).get("text") or ""

    def get_backend_name(self) -> str:
        return getattr(self._backend, "name", "disabled")


stt_daemon = DaemonSTT()

# Compatibilité historique avec stt_local
stt_local = stt_daemon

__all__ = [
    "DaemonSTT",
    "DaemonSTTBackend",
    "TranscriptionResult",
    "create_daemon_stt_backend",
    "stt_daemon",
    "stt_local",
]

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
from audio.engine_config import (
    FASTER_WHISPER_CACHE,
    FASTER_WHISPER_MODELS,
    is_valid_faster_whisper_model,
    model_missing_message,
    normalize_stt_engine,
)
from audio.resample import resample_pcm16_mono

logger = logging.getLogger(__name__)

STT_ENGINES = frozenset({
    "whisperkit", "whispercpp", "local", "faster-whisper", "none", "disabled",
})
FASTER_WHISPER_SIZES = FASTER_WHISPER_MODELS

# Phrase FR courte — JAMAIS une liste d'apps : Whisper republie le prompt
# sur du silence / audio illisible (bug Android M4A non décodé + bruit).
DEFAULT_INITIAL_PROMPT = (
    "Bonjour Monsieur. Conversation en français avec JARVIS."
)

# Tokens du prompt (hors mots courants) — détecte l'écho Whisper
_PROMPT_ECHO_STOPWORDS = frozenset({
    "bonjour", "monsieur", "conversation", "en", "français", "francais",
    "avec", "le", "la", "les", "un", "une", "des", "de", "du", "et", "a",
})


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
    # Clés propres uniquement (pas de liste d'apps UI) — max 8 pour limiter l'écho
    safe = [
        t for t in terms[:8]
        if t and t.lower() not in _PROMPT_ECHO_STOPWORDS
    ]
    if safe:
        base = f"{base} Vocabulaire : {', '.join(safe)}."
    return base


def _tokenize_stt_text(text: str) -> set[str]:
    import re

    return {
        tok
        for tok in re.findall(r"[a-z0-9àâäéèêëïîôùûüç'-]+", (text or "").lower())
        if tok and tok not in _PROMPT_ECHO_STOPWORDS and len(tok) > 1
    }


def is_stt_prompt_echo(transcript: str, prompt: str | None = None) -> bool:
    """True si Whisper a rétamé le ``initial_prompt`` au lieu d'entendre la parole.

    Cas typique : audio M4A non décodé, silence, ou bruit non vocal — le modèle
    recopie la liste de termes du prompt (ex. noms d'apps).
    """
    clean = (transcript or "").strip()
    if not clean:
        return True

    # Salutations / commandes courtes — jamais un écho de prompt
    norm = clean.lower().strip(".,!?;:… ")
    if norm in {
        "bonjour", "salut", "oui", "non", "merci", "jarvis", "stop",
        "ok", "d'accord", "hello", "hey",
    }:
        return False

    prompt_text = prompt if prompt is not None else build_initial_prompt()
    t_tokens = _tokenize_stt_text(clean)
    p_tokens = _tokenize_stt_text(prompt_text)

    # Ancien prompt liste-d'apps (régression connue) — rejet immédiat
    legacy_apps = {
        "messages", "mail", "calendar", "safari", "terminal",
        "visual", "studio", "code", "blue", "snowball", "deepseek",
    }
    if len(t_tokens & legacy_apps) >= 3:
        return True

    if not t_tokens:
        # Uniquement des stopwords (ex. "en français") → pas assez pour juger
        return False

    if not p_tokens:
        return False

    overlap = len(t_tokens & p_tokens) / len(t_tokens)
    # ≥70 % des mots du transcript viennent du prompt, et peu de contenu réel
    return overlap >= 0.7 and len(t_tokens - p_tokens) <= 2


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
        self._load_failed = False
        self._load_lock = asyncio.Lock()

    def preload_sync(self) -> bool:
        if self._loaded:
            return True
        if self._load_failed:
            return False
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            logger.error("[stt_daemon] faster-whisper absent — pip install faster-whisper")
            return False

        import os

        cpu_count = os.cpu_count() or 4
        num_workers = max(1, min(cpu_count // 2, 4))
        device = str(getattr(config, "STT_DEVICE", "auto") or "auto")
        compute_type = str(getattr(config, "STT_COMPUTE_TYPE", "auto") or "auto")
        allow_download = bool(getattr(config, "STT_ALLOW_MODEL_DOWNLOAD", False))
        try:
            self._model = WhisperModel(
                self._model_size,
                device=device,
                compute_type=compute_type,
                num_workers=num_workers,
                download_root=str(FASTER_WHISPER_CACHE),
                local_files_only=not allow_download,
            )
            self._loaded = True
            logger.info(
                "[stt_daemon] faster-whisper prêt (%s, device=%s, compute=%s)",
                self._model_size,
                device,
                compute_type,
            )
            return True
        except Exception as e:
            self._load_failed = True
            err_text = str(e).lower()
            if "local_files_only" in err_text or "not found" in err_text or "unable to open" in err_text:
                logger.error("[stt_daemon] %s", model_missing_message(self._model_size))
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
                    vad_filter=True,
                    condition_on_previous_text=False,
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
                        vad_filter=True,
                        condition_on_previous_text=False,
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


class FallbackSTTBackend(DaemonSTTBackend):
    """Chaîne locale ordonnée, sans aucun repli réseau."""

    name = "local-fallback"

    def __init__(self, backends: list[DaemonSTTBackend]) -> None:
        self._backends = backends
        self._active_index: int | None = None

    @property
    def active_backend(self) -> DaemonSTTBackend | None:
        if self._active_index is None:
            return None
        return self._backends[self._active_index]

    def preload_sync(self) -> bool:
        if self.active_backend is not None:
            return True
        for index, backend in enumerate(self._backends):
            if backend.preload_sync():
                self._active_index = index
                self.name = backend.name
                logger.info("[stt_daemon] Moteur local actif : %s", backend.name)
                return True
        logger.error("[stt_daemon] Aucun moteur STT local disponible")
        return False

    async def transcribe_pcm(
        self,
        pcm_bytes: bytes,
        *,
        sample_rate: int,
        language: str = "fr",
    ) -> TranscriptionResult | None:
        if not self.preload_sync() or self._active_index is None:
            return None

        for index in range(self._active_index, len(self._backends)):
            backend = self._backends[index]
            if index != self._active_index and not backend.preload_sync():
                continue
            result = await backend.transcribe_pcm(
                pcm_bytes, sample_rate=sample_rate, language=language,
            )
            if result is not None:
                self._active_index = index
                self.name = backend.name
                return result
            logger.warning("[stt_daemon] %s a échoué — essai du repli local", backend.name)
        return None


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


def _resolve_faster_whisper_model(explicit: str) -> str:
    model = (explicit or "").strip()
    if is_valid_faster_whisper_model(model):
        return model
    return getattr(config, "STT_MODEL", config.DEFAULT_STT_MODEL) or config.DEFAULT_STT_MODEL


def _build_faster_whisper_fallback_chain(
    primary_model: str,
    fallback_model: str,
) -> FallbackSTTBackend:
    primary = _resolve_faster_whisper_model(primary_model)
    fallback = _resolve_faster_whisper_model(fallback_model)
    whispercpp_model = str(
        getattr(
            config,
            "AUDIO_DAEMON_WHISPERCPP_MODEL_PATH",
            Path.home() / "models" / "ggml-large-v3.bin",
        )
    )
    backends: list[DaemonSTTBackend] = [FasterWhisperBackend(primary)]
    if fallback != primary:
        backends.append(FasterWhisperBackend(fallback))
    backends.extend([
        WhisperKitBackend("large-v3-v20240930_626MB"),
        WhisperCppBackend(whispercpp_model),
    ])
    return FallbackSTTBackend(backends)


def create_daemon_stt_backend() -> DaemonSTTBackend:
    engine = normalize_stt_engine(
        getattr(config, "STT_ENGINE", "") or getattr(config, "AUDIO_DAEMON_STT_ENGINE", "")
    )
    model = (getattr(config, "STT_MODEL", "") or getattr(config, "AUDIO_DAEMON_STT_MODEL", "") or "").strip()

    if engine in ("none", "disabled"):
        return DisabledSTT()
    if engine == "whisperkit":
        fallback_model = str(
            getattr(config, "STT_FALLBACK_MODEL", config.DEFAULT_STT_FALLBACK_MODEL)
            or config.DEFAULT_STT_FALLBACK_MODEL
        )
        whispercpp_model = str(
            getattr(
                config,
                "AUDIO_DAEMON_WHISPERCPP_MODEL_PATH",
                Path.home() / "models" / "ggml-large-v3.bin",
            )
        )
        return FallbackSTTBackend([
            WhisperKitBackend(model or "large-v3-v20240930_626MB"),
            WhisperCppBackend(whispercpp_model),
            FasterWhisperBackend(_resolve_faster_whisper_model(fallback_model)),
        ])
    if engine == "whispercpp":
        fallback_model = str(
            getattr(config, "STT_FALLBACK_MODEL", config.DEFAULT_STT_FALLBACK_MODEL)
            or config.DEFAULT_STT_FALLBACK_MODEL
        )
        return FallbackSTTBackend([
            WhisperCppBackend(
                model
                or str(getattr(
                    config,
                    "AUDIO_DAEMON_WHISPERCPP_MODEL_PATH",
                    Path.home() / "models" / "ggml-large-v3.bin",
                ))
            ),
            FasterWhisperBackend(_resolve_faster_whisper_model(fallback_model)),
        ])
    if engine in ("local", "faster-whisper", config.DEFAULT_STT_ENGINE):
        fallback_model = str(
            getattr(config, "STT_FALLBACK_MODEL", config.DEFAULT_STT_FALLBACK_MODEL)
            or config.DEFAULT_STT_FALLBACK_MODEL
        )
        return _build_faster_whisper_fallback_chain(model, fallback_model)
    logger.error("[stt_daemon] Moteur STT inconnu %r — désactivé", engine)
    return DisabledSTT()


class DaemonSTT:
    """Façade STT locale partagée par le daemon et les clients WebSocket."""

    def __init__(self) -> None:
        self._backend = create_daemon_stt_backend()
        self.available = not isinstance(self._backend, DisabledSTT)
        self._preload_attempted = False
        self.last_raw_text = ""
        self.last_clean_text = ""

    def preload_sync(self) -> bool:
        self._preload_attempted = True
        self.available = self._backend.preload_sync()
        return self.available

    async def transcribe_with_metadata(
        self,
        pcm_bytes: bytes,
        *,
        sample_rate: int | None = None,
        language: str = "fr",
    ) -> dict | None:
        if not self._preload_attempted:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.preload_sync)
        if not self.available:
            return None
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

    @staticmethod
    def _decode_media_bytes(audio_bytes: bytes) -> bytes:
        """Décode un conteneur audio navigateur en PCM16 mono 16 kHz."""
        try:
            from faster_whisper.audio import decode_audio
            import numpy as np  # type: ignore[import-untyped]
        except ImportError:
            logger.error(
                "[stt_daemon] Décodage média indisponible — installez faster-whisper"
            )
            return b""

        try:
            samples = decode_audio(io.BytesIO(audio_bytes), sampling_rate=16000)
            if samples is None or len(samples) == 0:
                return b""
            pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
            return pcm.tobytes()
        except Exception as exc:
            logger.warning("[stt_daemon] Décodage audio impossible : %s", exc)
            return b""

    async def transcribe(
        self,
        audio_bytes: bytes,
        language: str = "fr",
        timeout: float | None = None,
    ) -> str:
        """Transcrit du PCM ou un conteneur WebM/Opus, WAV, MP3 ou OGG localement."""
        if len(audio_bytes) < 1000:
            return ""

        from audio.audio_format import is_encoded_audio_container

        pcm_bytes = audio_bytes
        sample_rate = int(getattr(config, "AUDIO_DAEMON_SAMPLE_RATE", 16000))
        # Android Companion envoie AAC/M4A (ftyp @ offset 4) — sans décodage,
        # Whisper traite des octets compressés comme du PCM et hallucine le prompt.
        if is_encoded_audio_container(audio_bytes):
            loop = asyncio.get_running_loop()
            pcm_bytes = await loop.run_in_executor(None, self._decode_media_bytes, audio_bytes)
            sample_rate = 16000
        if not pcm_bytes:
            return ""

        operation = self.transcribe_with_metadata(
            pcm_bytes,
            sample_rate=sample_rate,
            language=language,
        )
        meta = await asyncio.wait_for(operation, timeout=timeout) if timeout else await operation
        text = str((meta or {}).get("text") or "").strip()
        self.last_raw_text = text
        self.last_clean_text = text
        return text

    async def transcribe_with_diarization(
        self,
        audio_bytes: bytes,
        language: str = "fr",
        timeout: float | None = None,
    ) -> list[dict]:
        """Retourne vide tant qu'aucun moteur local de diarisation n'est configuré."""
        return []

    def get_backend_name(self) -> str:
        return getattr(self._backend, "name", "disabled")


stt_daemon = DaemonSTT()

# Compatibilité historique avec stt_local
stt_local = stt_daemon

__all__ = [
    "DaemonSTT",
    "DaemonSTTBackend",
    "FallbackSTTBackend",
    "TranscriptionResult",
    "create_daemon_stt_backend",
    "stt_daemon",
    "stt_local",
]

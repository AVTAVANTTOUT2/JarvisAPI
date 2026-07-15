"""Configuration audio centralisée — helpers et journalisation de démarrage."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

FASTER_WHISPER_CACHE = Path.home() / ".cache" / "faster-whisper"
KOKORO_MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "kokoro"
SETUP_SCRIPT = "bash scripts/setup_local_audio.sh"

FASTER_WHISPER_MODELS = frozenset({
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v3", "large-v3-turbo",
})


def normalize_stt_engine(engine: str) -> str:
    """Alias historique ``local`` → ``faster-whisper``."""
    import config

    value = (engine or "").strip().lower()
    if value == "local":
        return config.DEFAULT_STT_ENGINE
    return value


def is_valid_faster_whisper_model(model: str) -> bool:
    name = (model or "").strip()
    if not name:
        return False
    if name in FASTER_WHISPER_MODELS:
        return True
    return name.startswith("large")


@dataclass(frozen=True)
class AudioEngineConfig:
    """Instantané des réglages audio actifs."""

    stt_engine: str
    stt_model: str
    stt_fallback_model: str
    stt_language: str
    stt_device: str
    stt_compute_type: str
    stt_allow_download: bool
    tts_engine: str
    kokoro_voice: str
    kokoro_lang: str


def load_audio_engine_config() -> AudioEngineConfig:
    import config

    return AudioEngineConfig(
        stt_engine=normalize_stt_engine(getattr(config, "STT_ENGINE", config.DEFAULT_STT_ENGINE)),
        stt_model=getattr(config, "STT_MODEL", config.DEFAULT_STT_MODEL),
        stt_fallback_model=getattr(config, "STT_FALLBACK_MODEL", config.DEFAULT_STT_FALLBACK_MODEL),
        stt_language=getattr(config, "STT_LANGUAGE", config.DEFAULT_STT_LANGUAGE),
        stt_device=getattr(config, "STT_DEVICE", config.DEFAULT_STT_DEVICE),
        stt_compute_type=getattr(config, "STT_COMPUTE_TYPE", config.DEFAULT_STT_COMPUTE_TYPE),
        stt_allow_download=bool(getattr(config, "STT_ALLOW_MODEL_DOWNLOAD", False)),
        tts_engine=(getattr(config, "TTS_ENGINE", config.DEFAULT_TTS_ENGINE) or config.DEFAULT_TTS_ENGINE).lower(),
        kokoro_voice=getattr(config, "KOKORO_VOICE", config.DEFAULT_KOKORO_VOICE),
        kokoro_lang=getattr(config, "KOKORO_LANG", config.DEFAULT_KOKORO_LANG),
    )


def log_audio_startup_config(*, active_stt_engine: str | None = None) -> None:
    """Journalise la pile audio au démarrage (sans secret)."""
    import config

    cfg = load_audio_engine_config()
    stt_active = active_stt_engine or cfg.stt_engine
    logger.info("STT engine: %s", stt_active)
    logger.info("STT model: %s", cfg.stt_model)
    logger.info("STT language: %s", cfg.stt_language)
    logger.info("TTS engine: %s", cfg.tts_engine)
    logger.info("Kokoro voice: %s", cfg.kokoro_voice)
    logger.info("Kokoro language: %s", cfg.kokoro_lang)
    logger.info("Cloud fallback: disabled")
    if not cfg.stt_allow_download:
        logger.info(
            "STT model download: disabled (cache=%s, setup=%s)",
            FASTER_WHISPER_CACHE,
            SETUP_SCRIPT,
        )


def model_missing_message(model_id: str) -> str:
    return (
        f"Modèle Whisper « {model_id} » absent dans {FASTER_WHISPER_CACHE}. "
        f"Téléchargement auto désactivé — exécutez : {SETUP_SCRIPT}"
    )


__all__ = [
    "AudioEngineConfig",
    "FASTER_WHISPER_CACHE",
    "FASTER_WHISPER_MODELS",
    "KOKORO_MODEL_DIR",
    "SETUP_SCRIPT",
    "is_valid_faster_whisper_model",
    "load_audio_engine_config",
    "log_audio_startup_config",
    "model_missing_message",
    "normalize_stt_engine",
]

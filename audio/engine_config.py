"""Configuration audio centralisée — helpers et journalisation de démarrage."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

FASTER_WHISPER_CACHE = Path.home() / ".cache" / "faster-whisper"
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
    # Réglages temps réel : versionnés dans `config`, exposés ici pour que le
    # démarrage et les diagnostics montrent ce qui est réellement appliqué.
    stt_beam_size: int
    stt_vad_filter: bool
    stt_quality_fallback_logprob: float
    stt_quality_fallback_min_speech_ms: int
    vad_silence_ms: int
    vad_min_speech_ms: int
    vad_pre_roll_ms: int
    # Synthèse vocale : un fournisseur local, une voix. Les réglages propres
    # au moteur vivent derrière l'interface `jarvis.audio.tts`, pas ici.
    tts_provider: str
    tts_model_path: str
    tts_voice: str
    tts_device: str


def load_audio_engine_config() -> AudioEngineConfig:
    import config

    from jarvis.audio.tts.config import load_tts_settings

    tts = load_tts_settings()
    return AudioEngineConfig(
        stt_engine=normalize_stt_engine(getattr(config, "STT_ENGINE", config.DEFAULT_STT_ENGINE)),
        stt_model=getattr(config, "STT_MODEL", config.DEFAULT_STT_MODEL),
        stt_fallback_model=getattr(config, "STT_FALLBACK_MODEL", config.DEFAULT_STT_FALLBACK_MODEL),
        stt_language=getattr(config, "STT_LANGUAGE", config.DEFAULT_STT_LANGUAGE),
        stt_device=getattr(config, "STT_DEVICE", config.DEFAULT_STT_DEVICE),
        stt_compute_type=getattr(config, "STT_COMPUTE_TYPE", config.DEFAULT_STT_COMPUTE_TYPE),
        stt_allow_download=bool(getattr(config, "STT_ALLOW_MODEL_DOWNLOAD", False)),
        stt_beam_size=int(getattr(config, "STT_BEAM_SIZE", config.DEFAULT_STT_BEAM_SIZE)),
        stt_vad_filter=bool(getattr(config, "STT_VAD_FILTER", config.DEFAULT_STT_VAD_FILTER)),
        stt_quality_fallback_logprob=float(getattr(
            config,
            "STT_QUALITY_FALLBACK_LOGPROB",
            config.DEFAULT_STT_QUALITY_FALLBACK_LOGPROB,
        )),
        stt_quality_fallback_min_speech_ms=int(getattr(
            config,
            "STT_QUALITY_FALLBACK_MIN_SPEECH_MS",
            config.DEFAULT_STT_QUALITY_FALLBACK_MIN_SPEECH_MS,
        )),
        vad_silence_ms=int(getattr(
            config, "AUDIO_DAEMON_SILENCE_MS", config.DEFAULT_AUDIO_DAEMON_SILENCE_MS)),
        vad_min_speech_ms=int(getattr(
            config, "AUDIO_DAEMON_MIN_SPEECH_MS", config.DEFAULT_AUDIO_DAEMON_MIN_SPEECH_MS)),
        vad_pre_roll_ms=int(getattr(
            config, "AUDIO_DAEMON_PRE_ROLL_MS", config.DEFAULT_AUDIO_DAEMON_PRE_ROLL_MS)),
        tts_provider=tts.provider,
        tts_model_path=tts.model_path,
        tts_voice=tts.voice_id,
        tts_device=tts.device,
    )


def log_audio_startup_config(*, active_stt_engine: str | None = None) -> None:
    """Journalise la pile audio au démarrage (sans secret)."""
    import config

    cfg = load_audio_engine_config()
    stt_active = active_stt_engine or cfg.stt_engine
    logger.info("STT engine: %s", stt_active)
    logger.info("STT model: %s", cfg.stt_model)
    logger.info("STT language: %s", cfg.stt_language)
    logger.info(
        "STT temps réel: compute=%s beam=%d vad_filter=%s "
        "quality_fallback<%.2f min_speech=%dms",
        cfg.stt_compute_type,
        cfg.stt_beam_size,
        cfg.stt_vad_filter,
        cfg.stt_quality_fallback_logprob,
        cfg.stt_quality_fallback_min_speech_ms,
    )
    logger.info(
        "VAD daemon: silence=%dms min_speech=%dms pre_roll=%dms",
        cfg.vad_silence_ms, cfg.vad_min_speech_ms, cfg.vad_pre_roll_ms,
    )
    logger.info("TTS provider: %s", cfg.tts_provider)
    logger.info("TTS model: %s", cfg.tts_model_path)
    logger.info("TTS voice: %s", cfg.tts_voice)
    logger.info("TTS device: %s", cfg.tts_device)
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
    "SETUP_SCRIPT",
    "is_valid_faster_whisper_model",
    "load_audio_engine_config",
    "log_audio_startup_config",
    "model_missing_message",
    "normalize_stt_engine",
]

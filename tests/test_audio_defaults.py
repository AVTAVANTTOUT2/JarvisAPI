"""Garde-fous et contrats des valeurs par défaut audio locales."""

from __future__ import annotations

import re
from pathlib import Path

import config
from audio.engine_config import FASTER_WHISPER_MODELS, is_valid_faster_whisper_model
from audio.stt_daemon import create_daemon_stt_backend, FasterWhisperBackend, FallbackSTTBackend


ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_DEFAULT_PATTERNS = (
    re.compile(r'(?<!DEFAULT_)AUDIO_DAEMON_STT_MODEL\s*=\s*["\']small["\']'),
    re.compile(r'_get\(\s*["\']AUDIO_DAEMON_STT_MODEL["\']\s*,\s*["\']small["\']'),
)

SCAN_PATHS = (
    ROOT / "config.py",
    ROOT / ".env.example",
    ROOT / ".env.config.example",
    ROOT / "jarvis" / "audio" / "tts" / "config.py",
    ROOT / "audio" / "stt_daemon.py",
    ROOT / "api" / "misc_integrations.py",
    ROOT / "api" / "chat_context.py",
    ROOT / "scripts" / "audio_daemon.py",
)


def test_canonical_builtin_defaults():
    """Constantes intégrées — indépendantes du .env utilisateur."""
    assert config.DEFAULT_TTS_PROVIDER == "qwen3_local"
    assert (
        config.DEFAULT_TTS_MODEL_PATH
        == "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-6bit"
    )
    assert config.DEFAULT_TTS_VOICE_PATH == "./voices/jarvis-fr"
    assert config.DEFAULT_TTS_DEVICE == "auto"
    assert config.DEFAULT_STT_ENGINE == "faster-whisper"
    assert config.DEFAULT_STT_MODEL == "large-v3-turbo"
    assert config.DEFAULT_STT_FALLBACK_MODEL == "large-v3"


def test_stt_engine_local_alias():
    from audio.engine_config import normalize_stt_engine

    assert normalize_stt_engine("local") == "faster-whisper"
    assert normalize_stt_engine("faster-whisper") == "faster-whisper"


def test_explicit_user_overrides_respected(monkeypatch):
    monkeypatch.setattr(config, "TTS_VOICE_PATH", "./voices/custom")
    monkeypatch.setattr(config, "STT_MODEL", "small")
    assert config.TTS_VOICE_PATH == "./voices/custom"
    assert config.STT_MODEL == "small"


def test_large_v3_turbo_accepted_in_validation():
    assert is_valid_faster_whisper_model("large-v3-turbo")
    assert "large-v3-turbo" in FASTER_WHISPER_MODELS


def test_faster_whisper_default_backend_chain(monkeypatch):
    monkeypatch.setattr("config.STT_ENGINE", "faster-whisper")
    monkeypatch.setattr("config.STT_MODEL", "large-v3-turbo")
    monkeypatch.setattr("config.STT_FALLBACK_MODEL", "large-v3")

    backend = create_daemon_stt_backend()
    assert isinstance(backend, FallbackSTTBackend)
    assert isinstance(backend._backends[0], FasterWhisperBackend)
    assert backend._backends[0]._model_size == "large-v3-turbo"
    assert backend._backends[1]._model_size == "large-v3"


def test_local_alias_maps_to_faster_whisper(monkeypatch):
    monkeypatch.setattr("config.STT_ENGINE", "local")
    backend = create_daemon_stt_backend()
    assert isinstance(backend, FallbackSTTBackend)
    assert isinstance(backend._backends[0], FasterWhisperBackend)


def test_no_forbidden_defaults_in_canonical_sources():
    offenders: list[str] = []
    for path in SCAN_PATHS:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_DEFAULT_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{path.relative_to(ROOT)}: {pattern.pattern}")
    assert offenders == [], "Valeurs par défaut obsolètes détectées :\n" + "\n".join(offenders)


def test_stt_backend_never_selects_cloud(monkeypatch):
    """create_daemon_stt_backend ne retourne jamais un moteur réseau."""
    monkeypatch.setattr("config.STT_ENGINE", "faster-whisper")
    backend = create_daemon_stt_backend()
    name = getattr(backend, "name", "")
    assert "cloud" not in name
    assert "eleven" not in name

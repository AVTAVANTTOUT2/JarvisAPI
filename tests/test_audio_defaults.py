"""Garde-fous et contrats des valeurs par défaut audio locales."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

import config
from audio.engine_config import FASTER_WHISPER_MODELS, is_valid_faster_whisper_model
from audio.stt_daemon import create_daemon_stt_backend, FasterWhisperBackend, FallbackSTTBackend
from audio.tts import get_tts_by_name


ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_DEFAULT_PATTERNS = (
    re.compile(r'(?<!DEFAULT_)TTS_ENGINE\s*=\s*["\']edge["\']'),
    re.compile(r'(?<!DEFAULT_)KOKORO_VOICE\s*=\s*["\']ff_siwis["\']'),
    re.compile(r'(?<!DEFAULT_)AUDIO_DAEMON_STT_MODEL\s*=\s*["\']small["\']'),
    re.compile(r'_get\(\s*["\']TTS_ENGINE["\']\s*,\s*["\']edge["\']'),
    re.compile(r'_get\(\s*["\']KOKORO_VOICE["\']\s*,\s*["\']ff_siwis["\']'),
    re.compile(r'_get\(\s*["\']AUDIO_DAEMON_STT_MODEL["\']\s*,\s*["\']small["\']'),
)

SCAN_PATHS = (
    ROOT / "config.py",
    ROOT / ".env.example",
    ROOT / ".env.config.example",
    ROOT / "audio" / "tts_native.py",
    ROOT / "audio" / "stt_daemon.py",
    ROOT / "api" / "misc_integrations.py",
    ROOT / "api" / "chat_context.py",
    ROOT / "scripts" / "audio_daemon.py",
)


def test_canonical_builtin_defaults():
    """Constantes intégrées — indépendantes du .env utilisateur."""
    assert config.DEFAULT_TTS_ENGINE == "kokoro"
    assert config.DEFAULT_KOKORO_VOICE == "af_nicole"
    assert config.DEFAULT_KOKORO_LANG == "fr-fr"
    assert config.DEFAULT_STT_ENGINE == "faster-whisper"
    assert config.DEFAULT_STT_MODEL == "large-v3-turbo"
    assert config.DEFAULT_STT_FALLBACK_MODEL == "large-v3"


def test_stt_engine_local_alias():
    from audio.engine_config import normalize_stt_engine

    assert normalize_stt_engine("local") == "faster-whisper"
    assert normalize_stt_engine("faster-whisper") == "faster-whisper"


def test_explicit_user_overrides_respected(monkeypatch):
    monkeypatch.setattr(config, "TTS_ENGINE", "edge")
    monkeypatch.setattr(config, "KOKORO_VOICE", "ff_siwis")
    monkeypatch.setattr(config, "STT_MODEL", "small")
    assert config.TTS_ENGINE == "edge"
    assert config.KOKORO_VOICE == "ff_siwis"
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


@pytest.mark.skipif(sys.platform != "darwin", reason="moteur macos (say) indisponible hors darwin")
def test_alternate_tts_engines_selectable():
    edge = get_tts_by_name("edge")
    macos = get_tts_by_name("macos")
    assert edge.get_backend_name() == "edge"
    assert macos.get_backend_name() == "macos"


def test_get_tts_kokoro_never_silently_returns_edge(monkeypatch):
    """Demander kokoro ne doit jamais basculer sur le singleton Edge."""
    from audio.tts import kokoro_tts

    monkeypatch.setattr(kokoro_tts, "available", False)
    engine = get_tts_by_name("kokoro")
    assert engine is kokoro_tts
    assert engine.get_backend_name() == "kokoro"


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

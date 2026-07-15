"""Garantit Edge Henri (FR masculin) pour le pipeline vocal Android."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_resolve_tts_engine_prefers_db_edge(monkeypatch):
    from audio.tts import resolve_tts_engine_name, resolve_tts_voice

    monkeypatch.setattr("config.TTS_ENGINE", "kokoro")
    monkeypatch.setattr("config.TTS_VOICE", "fr-FR-HenriNeural")
    with patch("database.get_setting", side_effect=lambda k, d="": "edge" if k == "tts_engine" else d):
        assert resolve_tts_engine_name() == "edge"
        assert resolve_tts_voice() == "fr-FR-HenriNeural"


def test_resolve_tts_engine_falls_back_to_config(monkeypatch):
    from audio.tts import resolve_tts_engine_name

    monkeypatch.setattr("config.TTS_ENGINE", "edge")
    with patch("database.get_setting", side_effect=lambda k, d="": d):
        assert resolve_tts_engine_name() == "edge"


@pytest.mark.asyncio
async def test_edge_henri_produces_mpeg_not_wav():
    """Henri Edge = MP3 (ID3/FFFB), pas WAV RIFF de Kokoro."""
    from audio.tts import get_tts_by_name

    engine = get_tts_by_name("edge")
    if not getattr(engine, "available", False):
        pytest.skip("edge-tts non installé")
    audio = await engine.synthesize("Bonjour Monsieur. Test de la voix française.")
    assert len(audio) > 1000
    # MP3 Edge : frame sync 0xFFEx ou tag ID3
    assert audio[:3] == b"ID3" or (audio[0] == 0xFF and (audio[1] & 0xE0) == 0xE0)
    assert audio[:4] != b"RIFF"

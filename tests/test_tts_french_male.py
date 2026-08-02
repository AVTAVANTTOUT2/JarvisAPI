"""Garantit Edge Henri (FR masculin) pour le pipeline vocal Android.

La synthèse Edge elle-même est couverte à deux niveaux :
  - `tests/test_tts_edge_unit.py` — `edge_tts` simulé, hors ligne, déterministe ;
  - `tests/test_tts_edge_external.py` — appel réel à Microsoft, marqueur
    `external_network`, exclu de la suite standard.
"""

from __future__ import annotations

from unittest.mock import patch


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


def test_resolved_voice_is_a_french_male_edge_voice(monkeypatch):
    """Une voix vide ferait parler Edge en anglais : le repli reste Henri."""
    from audio.tts import resolve_tts_voice

    monkeypatch.setattr("config.TTS_VOICE", "")
    assert resolve_tts_voice("edge") == "fr-FR-HenriNeural"


def test_edge_announces_mpeg_while_local_engines_announce_their_own_format():
    """Henri Edge = MP3 ; le WAV/M4A appartient aux moteurs locaux."""
    from audio.audio_format import tts_audio_mime

    assert tts_audio_mime("edge") == "audio/mpeg"
    assert tts_audio_mime("kokoro") == "audio/wav"
    assert tts_audio_mime("macos") == "audio/mp4"

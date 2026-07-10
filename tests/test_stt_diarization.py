"""Tests : diarisation STT (regroupement de mots en tours de parole)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_groups_consecutive_words_by_same_speaker():
    from audio.stt import group_words_into_turns

    result = {
        "text": "Bonjour comment vas tu",
        "words": [
            {"text": "Bonjour", "start": 0.0, "end": 0.5, "type": "word", "speaker_id": "speaker_0"},
            {"text": "comment", "start": 0.6, "end": 1.0, "type": "word", "speaker_id": "speaker_0"},
            {"text": "vas", "start": 1.1, "end": 1.3, "type": "word", "speaker_id": "speaker_0"},
            {"text": "tu", "start": 1.4, "end": 1.6, "type": "word", "speaker_id": "speaker_0"},
        ],
    }
    turns = group_words_into_turns(result)
    assert len(turns) == 1
    assert turns[0]["speaker_label"] == "A"
    assert turns[0]["text"] == "Bonjour comment vas tu"
    assert turns[0]["start_ms"] == 0
    assert turns[0]["end_ms"] == 1600


def test_splits_turn_when_speaker_changes():
    from audio.stt import group_words_into_turns

    result = {
        "words": [
            {"text": "Salut", "start": 0.0, "end": 0.3, "type": "word", "speaker_id": "speaker_0"},
            {"text": "Salut", "start": 0.5, "end": 0.8, "type": "word", "speaker_id": "speaker_1"},
            {"text": "toi", "start": 0.9, "end": 1.1, "type": "word", "speaker_id": "speaker_1"},
        ],
    }
    turns = group_words_into_turns(result)
    assert len(turns) == 2
    assert turns[0]["speaker_label"] == "A"
    assert turns[0]["text"] == "Salut"
    assert turns[1]["speaker_label"] == "B"
    assert turns[1]["text"] == "Salut toi"


def test_assigns_stable_labels_across_alternating_turns():
    from audio.stt import group_words_into_turns

    result = {
        "words": [
            {"text": "un", "start": 0.0, "end": 0.1, "type": "word", "speaker_id": "speaker_0"},
            {"text": "deux", "start": 0.2, "end": 0.3, "type": "word", "speaker_id": "speaker_1"},
            {"text": "trois", "start": 0.4, "end": 0.5, "type": "word", "speaker_id": "speaker_0"},
        ],
    }
    turns = group_words_into_turns(result)
    assert len(turns) == 3
    assert [t["speaker_label"] for t in turns] == ["A", "B", "A"]
    # speaker_0 reste "A" même en revenant après speaker_1 — pas un nouveau label


def test_ignores_spacing_pseudo_words():
    from audio.stt import group_words_into_turns

    result = {
        "words": [
            {"text": "Bonjour", "start": 0.0, "end": 0.5, "type": "word", "speaker_id": "speaker_0"},
            {"text": " ", "start": 0.5, "end": 0.5, "type": "spacing", "speaker_id": "speaker_0"},
            {"text": "toi", "start": 0.6, "end": 0.9, "type": "word", "speaker_id": "speaker_0"},
        ],
    }
    turns = group_words_into_turns(result)
    assert len(turns) == 1
    assert turns[0]["text"] == "Bonjour toi"


def test_degrades_to_single_turn_when_no_words_field():
    from audio.stt import group_words_into_turns

    turns = group_words_into_turns({"text": "Juste du texte, pas de diarisation."})
    assert len(turns) == 1
    assert turns[0]["speaker_label"] == "A"
    assert turns[0]["text"] == "Juste du texte, pas de diarisation."
    assert turns[0]["start_ms"] is None


def test_degrades_to_empty_when_no_text_and_no_words():
    from audio.stt import group_words_into_turns

    assert group_words_into_turns({}) == []


def test_empty_words_list_with_empty_text_returns_empty():
    from audio.stt import group_words_into_turns

    assert group_words_into_turns({"text": "", "words": []}) == []


@pytest.mark.asyncio
async def test_transcribe_with_diarization_calls_scribe_with_diarize_true(monkeypatch):
    import config
    from audio.stt import STT

    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "fake-key")
    instance = STT()

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "words": [
            {"text": "Oui", "start": 0.0, "end": 0.3, "type": "word", "speaker_id": "speaker_0"},
        ]
    }
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_response)

    with patch("audio.stt._get_http_client", return_value=mock_client):
        turns = await instance.transcribe_with_diarization(b"x" * 2000)

    assert len(turns) == 1
    assert turns[0]["text"] == "Oui"
    call_kwargs = mock_client.post.call_args.kwargs
    assert call_kwargs["data"]["diarize"] == "true"


@pytest.mark.asyncio
async def test_transcribe_with_diarization_unavailable_without_api_key(monkeypatch):
    import config
    from audio.stt import STT

    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    instance = STT()
    assert await instance.transcribe_with_diarization(b"x" * 2000) == []


@pytest.mark.asyncio
async def test_transcribe_with_diarization_too_short_audio(monkeypatch):
    import config
    from audio.stt import STT

    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "fake-key")
    instance = STT()
    assert await instance.transcribe_with_diarization(b"x" * 10) == []


@pytest.mark.asyncio
async def test_transcribe_with_diarization_handles_http_error_gracefully(monkeypatch):
    import httpx
    import config
    from audio.stt import STT

    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "fake-key")
    instance = STT()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

    with patch("audio.stt._get_http_client", return_value=mock_client):
        turns = await instance.transcribe_with_diarization(b"x" * 2000)

    assert turns == []

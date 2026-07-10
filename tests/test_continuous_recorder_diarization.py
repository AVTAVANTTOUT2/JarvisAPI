"""Tests : capture des tours de parole diarisés dans ContinuousRecording."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.DIARIZATION_ENABLED", True)
    from database import init_db

    init_db()
    return db_path


def _make_recording() -> int:
    from database import save_recording

    return save_recording(
        conversation_id=None, label="test", duration_seconds=60,
        transcription="x", summary="x", synthesis={}, actions={}, audio_size_kb=1,
    )


@pytest.mark.asyncio
async def test_captures_turns_for_single_chunk_recording(tmp_db):
    from audio.continuous_recorder import ContinuousRecording
    from database import get_conversation_turns

    rec = ContinuousRecording(conversation_id=1)
    rec.audio_chunks = [b"x" * 2000]
    rec_id = _make_recording()

    fake_stt = AsyncMock()
    fake_stt.transcribe_with_diarization = AsyncMock(return_value=[
        {"speaker_label": "A", "text": "Bonjour", "start_ms": 0, "end_ms": 500},
        {"speaker_label": "B", "text": "Salut", "start_ms": 600, "end_ms": 900},
    ])

    count = await rec._maybe_capture_turns(fake_stt, rec_id)

    assert count == 2
    stored = get_conversation_turns(rec_id)
    assert len(stored) == 2
    assert stored[0]["speaker_label"] == "A"


@pytest.mark.asyncio
async def test_multi_chunk_recording_diarized_on_concatenated_audio(tmp_db):
    """Plusieurs chunks MediaRecorder = fragments d'un même flux WebM — un seul
    appel STT sur leur concaténation (les labels ne sont cohérents qu'au sein
    d'un même appel)."""
    from audio.continuous_recorder import ContinuousRecording

    rec = ContinuousRecording(conversation_id=1)
    rec.audio_chunks = [b"x" * 2000, b"y" * 2000]
    rec_id = _make_recording()

    fake_stt = AsyncMock()
    fake_stt.transcribe_with_diarization = AsyncMock(return_value=[
        {"speaker_label": "A", "text": "Bonjour", "start_ms": 0, "end_ms": 100},
    ])

    count = await rec._maybe_capture_turns(fake_stt, rec_id)

    assert count == 1
    fake_stt.transcribe_with_diarization.assert_awaited_once()
    sent_audio = fake_stt.transcribe_with_diarization.call_args.args[0]
    assert sent_audio == b"x" * 2000 + b"y" * 2000


@pytest.mark.asyncio
async def test_oversized_audio_skips_diarization(tmp_db, monkeypatch):
    """Au-delà du seuil (100 Mo), on n'envoie pas l'audio au STT pour la diarisation."""
    import audio.continuous_recorder as cr
    from audio.continuous_recorder import ContinuousRecording

    monkeypatch.setattr(cr, "WARN_BYTES", 1000)
    rec = ContinuousRecording(conversation_id=1)
    rec.audio_chunks = [b"x" * 2000]
    rec_id = _make_recording()

    fake_stt = AsyncMock()
    count = await rec._maybe_capture_turns(fake_stt, rec_id)

    assert count == 0
    fake_stt.transcribe_with_diarization.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_via_config_flag(tmp_db, monkeypatch):
    from audio.continuous_recorder import ContinuousRecording

    monkeypatch.setattr("config.DIARIZATION_ENABLED", False)
    rec = ContinuousRecording(conversation_id=1)
    rec.audio_chunks = [b"x" * 2000]
    rec_id = _make_recording()

    fake_stt = AsyncMock()
    count = await rec._maybe_capture_turns(fake_stt, rec_id)

    assert count == 0
    fake_stt.transcribe_with_diarization.assert_not_called()


@pytest.mark.asyncio
async def test_no_turns_returned_gives_zero(tmp_db):
    from audio.continuous_recorder import ContinuousRecording

    rec = ContinuousRecording(conversation_id=1)
    rec.audio_chunks = [b"x" * 2000]
    rec_id = _make_recording()

    fake_stt = AsyncMock()
    fake_stt.transcribe_with_diarization = AsyncMock(return_value=[])

    assert await rec._maybe_capture_turns(fake_stt, rec_id) == 0


@pytest.mark.asyncio
async def test_stt_exception_handled_gracefully(tmp_db):
    from audio.continuous_recorder import ContinuousRecording

    rec = ContinuousRecording(conversation_id=1)
    rec.audio_chunks = [b"x" * 2000]
    rec_id = _make_recording()

    fake_stt = AsyncMock()
    fake_stt.transcribe_with_diarization = AsyncMock(side_effect=RuntimeError("boom"))

    assert await rec._maybe_capture_turns(fake_stt, rec_id) == 0

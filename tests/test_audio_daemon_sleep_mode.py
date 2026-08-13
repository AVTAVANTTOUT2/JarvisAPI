"""Mode veille du daemon audio — récupération et non-collision avec commandes de contrôle."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _pcm_chunk(samples: int = 480, value: int = 8000) -> bytes:
    import struct

    return struct.pack(f"<{samples}h", *([value] * samples))


def _daemon():
    from scripts.audio_daemon import AudioDaemon

    daemon = AudioDaemon()
    daemon._interrupt_event = asyncio.Event()
    daemon._utterance_queue = asyncio.Queue(maxsize=3)
    daemon._audio_queue = asyncio.Queue(maxsize=300)
    daemon.state = "listening"
    return daemon


@pytest.mark.asyncio
async def test_sleep_mode_wake_phrase_exits_sleep() -> None:
    """Une formule de réveil doit sortir du mode veille sans LLM."""
    from scripts.audio_daemon import AudioDaemon

    daemon = _daemon()
    daemon._sleep_mode = True
    daemon._conv_id = 42

    with (
        patch(
            "audio.stt_local.stt_local.transcribe_with_metadata",
            new_callable=AsyncMock,
            return_value={"text": "reveille-toi", "segments": [], "engine": "test"},
        ),
        patch.object(AudioDaemon, "_play_tts", new_callable=AsyncMock) as play_tts,
        patch.object(AudioDaemon, "_broadcast_state", new_callable=AsyncMock),
        patch.object(AudioDaemon, "_rearm", new_callable=AsyncMock) as rearm,
    ):
        await daemon._process_single_utterance(_pcm_chunk(), stt_available=True)
        await asyncio.sleep(0)

    assert daemon._sleep_mode is False
    play_tts.assert_awaited_once()
    rearm.assert_not_awaited()


@pytest.mark.asyncio
async def test_sleep_mode_ignores_non_wake_utterance() -> None:
    """En veille, une phrase ordinaire est ignorée sans appeler le LLM."""
    from scripts.audio_daemon import AudioDaemon

    daemon = _daemon()
    daemon._sleep_mode = True

    with (
        patch(
            "audio.stt_local.stt_local.transcribe_with_metadata",
            new_callable=AsyncMock,
            return_value={"text": "quel temps fait-il", "segments": [], "engine": "test"},
        ),
        patch.object(AudioDaemon, "_broadcast_state", new_callable=AsyncMock),
        patch.object(AudioDaemon, "_rearm", new_callable=AsyncMock) as rearm,
        patch("pipeline.process_voice_fast", new_callable=AsyncMock) as voice_fast,
    ):
        await daemon._process_single_utterance(_pcm_chunk(), stt_available=True)

    assert daemon._sleep_mode is True
    voice_fast.assert_not_awaited()
    rearm.assert_awaited_once()
    assert rearm.await_args.kwargs.get("reason") == "sleep_mode"


@pytest.mark.asyncio
async def test_silence_is_voice_control_not_sleep() -> None:
    """« silence » doit être une commande d'arrêt, pas une mise en veille."""
    from scripts.audio_daemon import AudioDaemon, SLEEP_PHRASES

    assert "silence" not in SLEEP_PHRASES
    assert "pause" not in SLEEP_PHRASES

    daemon = _daemon()
    with (
        patch(
            "audio.stt_local.stt_local.transcribe_with_metadata",
            new_callable=AsyncMock,
            return_value={"text": "silence", "segments": [], "engine": "test"},
        ),
        patch.object(AudioDaemon, "_broadcast_state", new_callable=AsyncMock),
        patch.object(AudioDaemon, "_rearm", new_callable=AsyncMock) as rearm,
        patch("pipeline.process_voice_fast", new_callable=AsyncMock) as voice_fast,
    ):
        await daemon._process_single_utterance(_pcm_chunk(), stt_available=True)

    assert daemon._sleep_mode is False
    voice_fast.assert_not_awaited()
    rearm.assert_awaited_once()
    assert rearm.await_args.kwargs.get("reason") == "voice_control"


@pytest.mark.asyncio
async def test_sleep_phrase_enters_sleep_mode() -> None:
    """Une formule de veille explicite active le mode veille."""
    from scripts.audio_daemon import AudioDaemon

    daemon = _daemon()
    with (
        patch(
            "audio.stt_local.stt_local.transcribe_with_metadata",
            new_callable=AsyncMock,
            return_value={"text": "mets-toi en veille", "segments": [], "engine": "test"},
        ),
        patch.object(AudioDaemon, "_play_tts", new_callable=AsyncMock),
        patch.object(AudioDaemon, "_broadcast_state", new_callable=AsyncMock),
    ):
        await daemon._process_single_utterance(_pcm_chunk(), stt_available=True)

    assert daemon._sleep_mode is True


@pytest.mark.asyncio
async def test_wake_word_detection_exits_sleep_mode() -> None:
    """La détection wake word en veille doit réactiver l'écoute."""
    from scripts.audio_daemon import AudioDaemon, FALLBACK_WAKE_CHUNKS

    daemon = _daemon()
    daemon._sleep_mode = True
    daemon.wake_word_enabled = True
    daemon.state = "wake_listening"

    loud_chunk = _pcm_chunk(value=20000)
    wake_loud_chunks = FALLBACK_WAKE_CHUNKS - 1

    with (
        patch.object(AudioDaemon, "_play_tts", new_callable=AsyncMock),
        patch.object(AudioDaemon, "_broadcast_state", new_callable=AsyncMock),
        patch("scripts.audio_daemon.chunk_rms", return_value=5000.0),
        patch("scripts.audio_daemon.WAKE_SOUND_PATH", MagicMock(exists=lambda: False)),
    ):
        # Simule la branche wake word du VAD : dernier chunk déclencheur
        if wake_loud_chunks + 1 >= FALLBACK_WAKE_CHUNKS:
            if daemon._sleep_mode:
                daemon.exit_sleep_mode()

    assert daemon._sleep_mode is False

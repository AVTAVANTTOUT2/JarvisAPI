"""La file de phrases vocales applique une backpressure sans perte."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from audio.voice_latency import UtteranceTrace
from scripts.audio_daemon import _enqueue_utterance_with_backpressure


@pytest.mark.asyncio
async def test_full_utterance_queue_waits_and_preserves_the_phrase():
    queue: asyncio.Queue[tuple[UtteranceTrace, bytes]] = asyncio.Queue(maxsize=1)
    existing = (UtteranceTrace(), b"premiere phrase")
    current = (UtteranceTrace(), b"phrase sous pression")
    queue.put_nowait(existing)

    enqueue = asyncio.create_task(
        _enqueue_utterance_with_backpressure(queue, current)
    )
    await asyncio.sleep(0)

    assert enqueue.done() is False
    assert await queue.get() is existing
    wait_ms = await asyncio.wait_for(enqueue, timeout=0.2)

    assert wait_ms >= 0
    assert await queue.get() is current


def test_native_daemon_has_no_complete_utterance_drop_branch():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "scripts" / "audio_daemon.py"
    ).read_text(encoding="utf-8")

    assert "utterance jetée" not in source
    assert "_enqueue_utterance_with_backpressure" in source


@pytest.mark.asyncio
async def test_post_tts_cleanup_preserves_queued_next_utterance():
    """Le tour suivant enfilé pendant processing ne doit pas être jeté."""
    from audio.voice_latency import UtteranceTrace
    from scripts.audio_daemon import AudioDaemon

    daemon = AudioDaemon()
    daemon._interrupt_event = asyncio.Event()
    daemon._utterance_queue = asyncio.Queue(maxsize=3)
    daemon._audio_queue = asyncio.Queue(maxsize=300)
    daemon.wake_word_enabled = False
    daemon.state = "processing"

    next_turn = (UtteranceTrace(), b"deuxieme phrase")
    daemon._utterance_queue.put_nowait(next_turn)

    with (
        patch("scripts.audio_daemon.create_conversation", return_value=1),
        patch(
            "scripts.audio_daemon.process_voice_fast",
            new_callable=AsyncMock,
            return_value={"text": "Réponse.", "emotion": "neutral", "latency_ms": 5},
        ),
        patch.object(type(daemon), "_play_tts", new_callable=AsyncMock),
        patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock),
        patch(
            "audio.stt_daemon.stt_daemon.transcribe_with_metadata",
            new_callable=AsyncMock,
            return_value={
                "text": "premiere phrase",
                "segments": [],
                "engine": "faster-whisper",
                "inference_ms": 5,
                "audio_ms": 900,
            },
        ),
    ):
        await daemon._process_single_utterance_active(
            b"\x00\x01" * 2000,
            True,
            trace=UtteranceTrace(),
        )

    assert daemon._utterance_queue.qsize() == 1
    assert await daemon._utterance_queue.get() is next_turn

"""La file de phrases vocales applique une backpressure sans perte."""

from __future__ import annotations

import asyncio

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

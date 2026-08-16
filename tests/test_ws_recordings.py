from __future__ import annotations

import pytest

from api.ws_recordings import WebSocketRecordingController


class _Socket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)


class _Recorder:
    def __init__(self, outcomes: list[object] | None = None) -> None:
        self.is_active = True
        self.label = "Test"
        self.outcomes = list(outcomes or [{"ok": True, "queued": True}])
        self.queue_calls = 0

    def add_chunk(self, _audio: bytes) -> None:
        pass

    def queue_for_processing(self) -> dict:
        self.queue_calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.asyncio
async def test_second_recording_start_is_rejected_without_losing_first() -> None:
    ws = _Socket()
    controller = WebSocketRecordingController()
    first = _Recorder()
    controller.active = first

    handled = await controller.handle_message(
        ws,
        {"type": "recording_start", "label": "Deuxième"},
        "recording_start",
        conversation_id=1,
        stt_available=True,
    )

    assert handled is True
    assert controller.active is first
    assert first.queue_calls == 0
    assert ws.messages[-1]["type"] == "error"


@pytest.mark.asyncio
async def test_failed_enqueue_keeps_spool_for_an_idempotent_retry() -> None:
    ws = _Socket()
    controller = WebSocketRecordingController()
    recorder = _Recorder([RuntimeError("db unavailable"), {"ok": True}])
    controller.active = recorder

    await controller.handle_message(
        ws,
        {"type": "recording_stop"},
        "recording_stop",
        conversation_id=1,
        stt_available=True,
    )
    assert controller.active is recorder
    assert ws.messages[-1]["result"]["retryable"] is True

    await controller.handle_message(
        ws,
        {"type": "recording_stop"},
        "recording_stop",
        conversation_id=1,
        stt_available=True,
    )
    assert controller.active is None
    assert recorder.queue_calls == 2


def test_disconnect_retry_does_not_drop_failed_spool() -> None:
    controller = WebSocketRecordingController()
    recorder = _Recorder([RuntimeError("db unavailable"), {"ok": True}])
    controller.active = recorder

    controller.close()
    assert controller.active is recorder

    controller.close()
    assert controller.active is None
    assert recorder.queue_calls == 2

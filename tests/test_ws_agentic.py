from __future__ import annotations

from types import SimpleNamespace

import pytest

from api import ws_agentic, ws_client_context


class _WebSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_json(self, payload: dict[str, object]) -> None:
        self.messages.append(payload)


def test_client_context_parser_keeps_authenticated_device_and_bounds_metadata() -> None:
    base = ws_client_context.AgenticClientContext.from_values(
        device="trusted-mobile",
        device_locked=True,
    )

    _, message_type, message_id, current = (
        ws_client_context.parse_websocket_client_message(
            '{"type":"text","client_message_id":"m1","device":"spoofed",'
            '"locale":"fr-CA","timezone":"America/Toronto"}',
            base,
        )
    )

    assert (message_type, message_id) == ("text", "m1")
    assert current.device == "trusted-mobile"
    assert (current.locale, current.timezone_name) == ("fr-CA", "America/Toronto")


@pytest.mark.asyncio
async def test_websocket_agentic_propagates_client_context(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _start(_request, _conversation_id, **kwargs):
        captured.update(kwargs)
        return {
            "text": "Tâche lancée",
            "agentic_run": {
                "run_id": "run-context",
                "status": "running",
                "phase": "running",
                "category": "workflow",
            },
        }

    monkeypatch.setattr(ws_agentic, "maybe_start_agentic_run", _start)
    ws = _WebSocket()

    await ws_agentic.maybe_send_agentic_run(
        ws,
        "/agent prépare le rapport",
        42,
        voice_mode=False,
        send_tts=False,
        idempotency_key="ws:context:1",
        device="android-42",
        locale="fr-CA",
        timezone_name="America/Toronto",
    )

    assert captured["device"] == "android-42"
    assert captured["locale"] == "fr-CA"
    assert captured["timezone_name"] == "America/Toronto"


class _Service:
    async def wait_for_terminal(self, run_id: str, timeout: float | None = None):
        assert run_id == "run-safe"
        assert timeout is not None
        return SimpleNamespace(status=SimpleNamespace(value="completed"))


@pytest.mark.asyncio
async def test_terminal_voice_summary_is_constant_redacted_and_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spoken: list[str] = []

    async def _tts(_ws, text: str, _emotion: str) -> None:
        spoken.append(text)

    monkeypatch.setattr(ws_agentic, "_send_tts_streaming", _tts)
    ws_agentic._voice_summaries_delivered.clear()
    ws = _WebSocket()

    await ws_agentic._send_terminal_voice_summary(
        ws, "run-safe", service=_Service()
    )
    await ws_agentic._send_terminal_voice_summary(
        ws, "run-safe", service=_Service()
    )

    assert spoken == ["La tâche est terminée et vérifiée par JARVIS."]
    assert len(ws.messages) == 1
    payload = ws.messages[0]
    assert payload["type"] == "agentic_voice_summary"
    assert payload["status"] == "completed"
    assert "/" not in str(payload["content"])
    assert "token" not in str(payload["content"]).lower()

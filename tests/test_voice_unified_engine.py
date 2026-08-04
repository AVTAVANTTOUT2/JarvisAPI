"""Contrats du moteur de tour unique partagé par toutes les surfaces vocales."""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_voice_adapter_delegates_actions_to_canonical_engine() -> None:
    from api.voice_processing import _process_voice_fast

    action = {"type": "weather", "city": "Lille"}
    action_result = {"ok": True, "message": "12 °C"}
    canonical = AsyncMock(return_value={
        "text": "Il fait douze degrés à Lille.",
        "emotion": "neutral",
        "action": action,
        "action_result": action_result,
        "agent": "info",
        "model": "deepseek-fast",
        "cost": 0.001,
    })

    with patch(
        "api.voice_cognitive.maybe_handle_cognitive_voice",
        AsyncMock(return_value=None),
    ), patch(
        "api.voice_processing.maybe_handle_fitness_voice", return_value=None,
    ), patch(
        "api.voice_processing._process_message_internal", canonical,
    ), patch(
        "api.voice_processing._persist_voice_messages_async",
    ) as persist, patch(
        "api.voice_processing._broadcast_voice_debug", AsyncMock(),
    ), patch(
        "api.voice_processing._save_voice_debug_trace", return_value=17,
    ):
        result = await _process_voice_fast(
            "Quel temps fait-il ?",
            42,
            confirmation_session_id="voice:desktop",
        )

    canonical.assert_awaited_once_with(
        "Quel temps fait-il ?",
        42,
        voice_mode=True,
        confirmation_session_id="voice:desktop",
        persist_assistant=False,
        trace=None,
    )
    persist.assert_called_once()
    assert result["action"] == action
    assert result["action_result"] == action_result
    assert result["text"] == "Il fait douze degrés à Lille."
    assert result["trace_id"] == 17


@pytest.mark.asyncio
async def test_voice_adapter_keeps_clipboard_result_local_only() -> None:
    from api.voice_processing import _process_voice_fast

    secret = "presse-papiers-secret"
    canonical = AsyncMock(return_value={
        "text": "Le presse-papiers a bien été lu localement.",
        "emotion": "neutral",
        "action": {"type": "clipboard", "action": "get"},
        "action_result": {"ok": True, "content": secret},
        "agent": "productivity",
        "model": "deepseek-fast",
        "cost": 0.0,
    })
    saved_trace: dict = {}

    def _save_trace(value: dict) -> int:
        saved_trace.update(value)
        return 18

    with patch(
        "api.voice_cognitive.maybe_handle_cognitive_voice",
        AsyncMock(return_value=None),
    ), patch(
        "api.voice_processing.maybe_handle_fitness_voice", return_value=None,
    ), patch(
        "api.voice_processing._process_message_internal", canonical,
    ), patch(
        "api.voice_processing._persist_voice_messages_async",
    ), patch(
        "api.voice_processing._broadcast_voice_debug", AsyncMock(),
    ), patch(
        "api.voice_processing._save_voice_debug_trace", side_effect=_save_trace,
    ):
        result = await _process_voice_fast("Lis le presse-papiers", 43)

    assert result["action_result"] == {"ok": True}
    assert saved_trace["action_result"] == "[LOCAL_ONLY]"
    assert secret not in repr(result)
    assert secret not in repr(saved_trace)


@pytest.mark.asyncio
async def test_anticipatory_speech_runs_concurrently_with_canonical_turn() -> None:
    from api.voice_processing import _process_voice_fast

    canonical_started = asyncio.Event()
    ack_started = asyncio.Event()
    release = asyncio.Event()

    async def _canonical(*_args, **_kwargs):
        canonical_started.set()
        await release.wait()
        return {
            "text": "Réponse finale.",
            "emotion": "neutral",
            "action": None,
            "action_result": None,
            "agent": "info",
            "model": "deepseek-fast",
            "cost": 0.0,
        }

    async def _ack() -> None:
        ack_started.set()
        await release.wait()

    with patch(
        "api.voice_cognitive.maybe_handle_cognitive_voice",
        AsyncMock(return_value=None),
    ), patch(
        "api.voice_processing.maybe_handle_fitness_voice", return_value=None,
    ), patch(
        "api.voice_processing._process_message_internal", side_effect=_canonical,
    ), patch(
        "api.voice_processing._persist_voice_messages_async",
    ), patch(
        "api.voice_processing._broadcast_voice_debug", AsyncMock(),
    ), patch(
        "api.voice_processing._save_voice_debug_trace", return_value=19,
    ):
        task = asyncio.create_task(
            _process_voice_fast(
                "Donne-moi la météo",
                45,
                on_canonical_turn_started=_ack,
            )
        )
        await asyncio.wait_for(canonical_started.wait(), timeout=1)
        await asyncio.wait_for(ack_started.wait(), timeout=1)
        assert task.done() is False
        release.set()
        result = await task

    assert result["text"] == "Réponse finale."


@pytest.mark.asyncio
async def test_deterministic_fastpath_never_plays_anticipatory_speech() -> None:
    from api.voice_processing import _process_voice_fast

    ack = AsyncMock()
    with patch(
        "api.voice_processing._persist_voice_messages_async",
    ):
        result = await _process_voice_fast(
            "Jarvis ?",
            46,
            on_canonical_turn_started=ack,
        )

    assert result["model"] == "hail"
    ack.assert_not_awaited()


@pytest.mark.asyncio
async def test_canonical_engine_can_defer_voice_persistence() -> None:
    from api.chat_processing import _process_message_internal
    from audio.voice_latency import UtteranceTrace

    canonical = AsyncMock(return_value={
        "response": "[neutral] Réponse canonique.",
        "emotion": "neutral",
        "agent": "info",
        "model": "deepseek-fast",
        "tokens_in": 2,
        "tokens_out": 3,
        "cost": 0.0,
    })
    trace = UtteranceTrace()

    with patch(
        "api.chat_processing.orchestrator.handle", canonical,
    ), patch(
        "api.chat_processing._build_enriched_context", AsyncMock(return_value={}),
    ), patch(
        "api.chat_processing.save_message",
    ) as save_message, patch(
        "api.chat_processing.update_conversation_activity",
    ), patch(
        "api.chat_processing._maybe_title_conversation", AsyncMock(),
    ):
        result = await _process_message_internal(
            "Question vocale",
            44,
            voice_mode=True,
            persist_assistant=False,
            trace=trace,
        )

    assert result["text"] == "Réponse canonique."
    save_message.assert_not_called()
    events = [mark.event for mark in trace.marks]
    assert events == [
        "context.build.started",
        "context.build.completed",
        "llm.queue.entered",
        "llm.request.started",
        "llm.completed",
    ]


def test_voice_module_contains_no_second_action_or_llm_engine() -> None:
    from api import voice_processing

    source = inspect.getsource(voice_processing)
    assert "_process_message_internal(" in source
    assert "llm.chat(" not in source
    assert "execute_action(" not in source
    assert "build_voice_system_prompt(" not in source
    assert "```action" not in source


def test_voice_transports_publish_structured_action_fields() -> None:
    from api import mobile_voice_service, ws_handsfree
    from scripts import audio_daemon

    for module in (mobile_voice_service, ws_handsfree, audio_daemon):
        source = inspect.getsource(module)
        assert 'get("action")' in source
        assert 'get("action_result")' in source

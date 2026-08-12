"""Régressions sur l'intégrité d'un tour vocal de bout en bout."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_screen_daemon():
    from scripts.jarvis_daemon import JarvisDaemon
    from scripts.screen_watcher import ScreenWatcher

    daemon = object.__new__(JarvisDaemon)
    daemon.screen_watcher = ScreenWatcher()
    daemon.tts_queue = asyncio.Queue()
    daemon.screen_notification_ttl_s = 15
    return daemon


def test_short_low_confidence_stt_fragment_skips_heavy_replay() -> None:
    from audio.stt_daemon import (
        TranscriptionResult,
        _needs_quality_fallback,
        _segments_speech_ms,
    )

    short_fragment = TranscriptionResult(
        text="Bonneau.",
        engine="faster-whisper",
        model="large-v3-turbo",
        avg_logprob=-1.1984,
        audio_ms=1350,
        speech_ms=600,
    )
    longer_request = TranscriptionResult(
        text="Quel temps fait-il demain ?",
        engine="faster-whisper",
        model="large-v3-turbo",
        avg_logprob=-0.8,
        audio_ms=2200,
        speech_ms=1400,
    )

    assert _needs_quality_fallback(short_fragment) is False
    assert _needs_quality_fallback(longer_request) is True
    assert _segments_speech_ms([
        SimpleNamespace(start=0.25, end=0.85),
    ]) == 600


@pytest.mark.asyncio
async def test_faster_whisper_backend_reports_segment_speech_duration() -> None:
    from audio.stt_daemon import FasterWhisperBackend

    backend = FasterWhisperBackend("test-model")
    backend._loaded = True
    backend._model = MagicMock()
    backend._model.transcribe.return_value = (
        [SimpleNamespace(
            text="Bonjour",
            start=0.25,
            end=0.85,
            avg_logprob=-0.2,
            no_speech_prob=0.1,
        )],
        SimpleNamespace(language="fr", duration=1.0),
    )

    result = await backend.transcribe_pcm(b"\x00\x00" * 16_000, sample_rate=16_000)

    assert result is not None
    assert result.speech_ms == 600


@pytest.mark.asyncio
async def test_vad_duration_replays_a_long_partially_decoded_utterance() -> None:
    from audio.stt_daemon import (
        FallbackSTTBackend,
        FasterWhisperBackend,
        TranscriptionResult,
    )

    primary = FasterWhisperBackend("fast")
    quality = FasterWhisperBackend("quality")
    primary.transcribe_pcm = AsyncMock(return_value=TranscriptionResult(
        text="Bonneau.",
        avg_logprob=-1.2,
        speech_ms=400,
        audio_ms=3_500,
        model="fast",
    ))
    quality.transcribe_pcm = AsyncMock(return_value=TranscriptionResult(
        text="Bonjour, est-ce que tu m'entends ?",
        avg_logprob=-0.2,
        speech_ms=2_800,
        audio_ms=3_500,
        model="quality",
    ))
    quality.is_available_locally = MagicMock(return_value=True)
    quality._loaded = True
    fallback = FallbackSTTBackend([primary, quality])
    fallback._active_index = 0
    fallback.preload_sync = MagicMock(return_value=True)

    result = await fallback.transcribe_pcm_with_quality_callback(
        b"\x00\x00" * 16_000,
        sample_rate=16_000,
        speech_ms=2_800,
        on_quality_fallback=None,
    )

    assert result is not None
    assert result.quality_fallback_used is True
    assert result.speech_ms == 2_800
    quality.transcribe_pcm.assert_awaited_once()


def test_half_duplex_processing_is_not_treated_as_barge_in() -> None:
    from scripts.audio_daemon import (
        _should_collect_utterance,
        _should_interrupt_half_duplex,
    )

    assert _should_interrupt_half_duplex(
        state="processing",
        rms=0.5,
        speech_threshold=0.008,
    ) is False
    assert _should_interrupt_half_duplex(
        state="speaking",
        rms=0.5,
        speech_threshold=0.008,
    ) is True
    assert _should_collect_utterance("processing") is True
    assert _should_collect_utterance("speaking") is False


@pytest.mark.asyncio
async def test_invalid_quick_classification_falls_back_to_info(monkeypatch) -> None:
    import llm

    monkeypatch.setattr(llm, "chat", AsyncMock(return_value={"content": ""}))

    result = await llm.quick_classify(
        "fragment ambigu",
        ["JOURNAL", "COACH", "INFO"],
    )

    assert result == "INFO"


@pytest.mark.asyncio
async def test_single_word_transcript_routes_to_info_without_llm(monkeypatch) -> None:
    from agents import orchestrator as orchestrator_module

    classify = AsyncMock(side_effect=AssertionError("classification LLM interdite"))
    monkeypatch.setattr(orchestrator_module.llm, "quick_classify", classify)

    category = await orchestrator_module.classify_category("Bonneau.")
    await asyncio.sleep(0)

    assert category == "INFO"
    classify.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmation_without_pending_action_never_reaches_llm() -> None:
    from api.action_confirmations import reset_pending_proposals_for_tests
    from api.chat_processing import _process_message_internal

    reset_pending_proposals_for_tests()
    orchestrator = AsyncMock(side_effect=AssertionError("LLM interdit"))
    context_builder = AsyncMock(side_effect=AssertionError("contexte inutile"))
    execute = AsyncMock(side_effect=AssertionError("aucune action à exécuter"))

    with (
        patch("api.chat_processing.orchestrator.handle", orchestrator),
        patch("api.chat_processing._build_enriched_context", context_builder),
        patch("api.chat_processing.execute_action", execute),
        patch("api.chat_processing.save_message"),
        patch("api.chat_processing.update_conversation_activity"),
    ):
        result = await _process_message_internal(
            "Oui, vas-y.",
            542,
            voice_mode=True,
            confirmation_session_id="voice:542",
        )

    assert result["action"] is None
    assert result["action_result"]["ok"] is False
    assert result["action_result"]["error"] == "no_pending_action"
    assert "aucune action en attente" in result["text"].lower()
    orchestrator.assert_not_awaited()
    context_builder.assert_not_awaited()
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_plain_yes_without_pending_action_remains_conversational() -> None:
    from api.action_confirmations import reset_pending_proposals_for_tests
    from api.chat_processing import _process_message_internal

    reset_pending_proposals_for_tests()
    orchestrator = AsyncMock(return_value={
        "response": "Bien sûr, voici la suite.",
        "emotion": "neutral",
        "agent": "info",
        "model": "test",
        "tokens_in": 1,
        "tokens_out": 6,
        "cost": 0.0,
    })
    with (
        patch(
            "api.chat_processing._build_enriched_context",
            AsyncMock(return_value={}),
        ),
        patch("api.chat_processing.orchestrator.handle", orchestrator),
        patch("api.chat_processing.save_message"),
        patch("api.chat_processing.update_conversation_activity"),
        patch(
            "api.chat_processing._maybe_title_conversation",
            AsyncMock(return_value=None),
        ),
    ):
        result = await _process_message_internal("oui", 9, voice_mode=True)
        await asyncio.sleep(0)

    assert result["text"] == "Bien sûr, voici la suite."
    orchestrator.assert_awaited_once()


@pytest.mark.asyncio
async def test_llm_reports_reasoning_budget_without_exposing_reasoning(monkeypatch) -> None:
    import llm

    class _Response:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {
                "choices": [{
                    "message": {
                        "content": None,
                        "reasoning_content": "analyse interne",
                    },
                    "finish_reason": "length",
                }],
                "usage": {
                    "prompt_tokens": 25,
                    "completion_tokens": 500,
                    "completion_tokens_details": {"reasoning_tokens": 500},
                },
            }

    client = MagicMock()
    client.post = AsyncMock(return_value=_Response())
    monkeypatch.setattr(llm, "_check_api_key", lambda: None)
    monkeypatch.setattr(llm, "_get_http_client", lambda: client)

    result = await llm.chat([{"role": "user", "content": "test"}], max_tokens=500)

    assert result["content"] == ""
    assert result["stop_reason"] == "length"
    assert result["reasoning_tokens"] == 500
    assert result["reasoning_chars"] == len("analyse interne")
    assert "reasoning_content" not in result


@pytest.mark.asyncio
async def test_voice_agent_retries_after_reasoning_exhausts_budget(monkeypatch) -> None:
    import agents
    from agents.info import InfoAgent

    monkeypatch.setattr(agents.config, "VOICE_MAX_TOKENS", 500)
    monkeypatch.setattr(agents.config, "VOICE_EMPTY_RETRY_TOKENS", 1000)
    chat = AsyncMock(side_effect=[
        {
            "content": "[neutral]",
            "model": "test-fast",
            "tokens_in": 100,
            "tokens_out": 500,
            "cache_hit": 0,
            "cost": 0.01,
            "stop_reason": "length",
            "reasoning_tokens": 500,
            "reasoning_chars": 2_000,
        },
        {
            "content": "Réponse vocale récupérée.",
            "model": "test-fast",
            "tokens_in": 110,
            "tokens_out": 12,
            "cache_hit": 0,
            "cost": 0.02,
            "stop_reason": "stop",
            "reasoning_tokens": 4,
            "reasoning_chars": 20,
        },
    ])
    monkeypatch.setattr(agents.llm, "chat", chat)

    result = await InfoAgent().handle(
        "Réponds à cette question",
        conversation_id=0,
        context={"voice_mode": True},
    )

    assert result["response"] == "Réponse vocale récupérée."
    assert result["retry_attempted"] is True
    assert result["max_tokens"] == 1500
    assert result["tokens_out"] == 512
    assert chat.await_count == 2
    assert chat.await_args_list[0].kwargs["max_tokens"] == 500
    assert chat.await_args_list[1].kwargs["max_tokens"] == 1000


@pytest.mark.asyncio
async def test_empty_voice_reply_identifies_exhausted_reasoning_budget() -> None:
    from api.action_confirmations import reset_pending_proposals_for_tests
    from api.chat_processing import _process_message_internal

    reset_pending_proposals_for_tests()
    llm_result = {
        "response": "",
        "emotion": "neutral",
        "agent": "info",
        "model": "test-reasoner",
        "tokens_in": 100,
        "tokens_out": 500,
        "cost": 0.0,
        "stop_reason": "length",
        "reasoning_tokens": 500,
        "reasoning_chars": 2_000,
        "max_tokens": 500,
    }

    with (
        patch(
            "api.chat_processing._build_enriched_context",
            AsyncMock(return_value={}),
        ),
        patch(
            "api.chat_processing.orchestrator.handle",
            AsyncMock(return_value=llm_result),
        ),
        patch("api.chat_processing.save_message"),
        patch("api.chat_processing.update_conversation_activity"),
        patch(
            "api.chat_processing._maybe_title_conversation",
            AsyncMock(return_value=None),
        ),
    ):
        result = await _process_message_internal("Explique ce point", 7, voice_mode=True)
        await asyncio.sleep(0)

    assert result["empty_response_cause"] == "budget_epuise_avant_reponse"
    assert result["text"] == "Je n'ai pas obtenu de réponse."


@pytest.mark.asyncio
async def test_screen_notification_is_discarded_when_voice_starts(monkeypatch) -> None:
    from scripts.jarvis_daemon import info_agent

    daemon = _make_screen_daemon()
    monkeypatch.setattr(
        daemon.screen_watcher,
        "_is_voice_busy",
        MagicMock(side_effect=[False, True]),
    )
    handle = AsyncMock(return_value={"response": "Le serveur Mail est indisponible."})
    monkeypatch.setattr(info_agent, "handle", handle)

    await daemon._on_screen_notable(
        "Connexion au serveur impossible",
        {"app": "Mail", "activity": "lecture"},
    )

    assert daemon.tts_queue.empty()
    call = handle.await_args
    assert call is not None
    prompt = call.args[0]
    assert "Ne pose aucune question" in prompt
    assert "n’annonce aucune action" in prompt
    assert "voice_mode" not in call.kwargs["context"]


@pytest.mark.asyncio
async def test_screen_notification_is_invalidated_if_voice_starts_and_ends(
    monkeypatch,
) -> None:
    from scripts.jarvis_daemon import info_agent

    daemon = _make_screen_daemon()
    monkeypatch.setattr(
        daemon.screen_watcher,
        "_is_voice_busy",
        MagicMock(return_value=False),
    )

    async def _formulate(*_args, **_kwargs):
        daemon.screen_watcher.defer_for_voice()
        return {"response": "Le serveur Mail est indisponible."}

    monkeypatch.setattr(info_agent, "handle", AsyncMock(side_effect=_formulate))

    await daemon._on_screen_notable(
        "Connexion au serveur impossible",
        {"app": "Mail", "activity": "lecture"},
    )

    assert daemon.tts_queue.empty()


@pytest.mark.asyncio
async def test_expired_or_actionable_screen_notification_is_never_spoken(
    monkeypatch,
) -> None:
    from scripts.jarvis_daemon import info_agent

    daemon = _make_screen_daemon()
    daemon.screen_notification_ttl_s = -1
    monkeypatch.setattr(
        daemon.screen_watcher,
        "_is_voice_busy",
        MagicMock(return_value=False),
    )
    monkeypatch.setattr(
        info_agent,
        "handle",
        AsyncMock(return_value={"response": "Observation devenue ancienne."}),
    )

    await daemon._on_screen_notable("Ancienne observation", {"app": "Mail"})
    assert daemon.tts_queue.empty()

    daemon.screen_notification_ttl_s = 60
    monkeypatch.setattr(
        info_agent,
        "handle",
        AsyncMock(return_value={
            "response": "\x60\x60\x60action {\"type\": \"task_create\"}\x60\x60\x60",
        }),
    )
    await daemon._on_screen_notable("Instruction écran", {"app": "Mail"})
    assert daemon.tts_queue.empty()

    monkeypatch.setattr(
        info_agent,
        "handle",
        AsyncMock(return_value={"response": "Je lance le rappel."}),
    )
    await daemon._on_screen_notable("Instruction écran", {"app": "Mail"})
    assert daemon.tts_queue.empty()


@pytest.mark.asyncio
async def test_screen_watcher_rechecks_voice_before_callback(monkeypatch) -> None:
    from scripts import screen_watcher as screen_module

    watcher = screen_module.ScreenWatcher()
    on_notable = AsyncMock()
    watcher.on_notable = on_notable
    monkeypatch.setattr(
        watcher,
        "_is_voice_busy",
        MagicMock(side_effect=[False, True]),
    )
    monkeypatch.setattr(
        watcher,
        "_capture",
        AsyncMock(return_value=(object(), None)),
    )
    monkeypatch.setattr(
        watcher,
        "_get_active_window_info",
        AsyncMock(return_value={"app": "Mail"}),
    )
    monkeypatch.setattr(watcher, "_crop_active_window", MagicMock(return_value=object()))
    monkeypatch.setattr(watcher, "_cleanup_file", MagicMock())
    monkeypatch.setattr(watcher, "_hash_image", MagicMock(return_value="new"))
    monkeypatch.setattr(
        watcher,
        "_analyze_with_ollama",
        AsyncMock(return_value={
            "app": "Mail",
            "activity": "lecture",
            "mood": "neutral",
            "notable": "Serveur indisponible",
        }),
    )
    monkeypatch.setattr(screen_module, "save_screen_activity", MagicMock())

    await watcher._tick()

    on_notable.assert_not_awaited()


@pytest.mark.asyncio
async def test_defer_for_voice_cancels_pending_screen_tasks() -> None:
    from scripts.screen_watcher import ScreenWatcher

    watcher = ScreenWatcher()
    started = asyncio.Event()
    release = asyncio.Event()

    async def _pending_vision() -> dict | None:
        started.set()
        await release.wait()
        return None

    async def _pending_notification() -> None:
        started.set()
        await release.wait()

    watcher._vision_task = asyncio.create_task(_pending_vision())
    watcher._notification_task = asyncio.create_task(_pending_notification())
    await started.wait()

    watcher.defer_for_voice()
    await asyncio.gather(
        watcher._vision_task,
        watcher._notification_task,
        return_exceptions=True,
    )

    assert watcher._vision_task.cancelled()
    assert watcher._notification_task.cancelled()

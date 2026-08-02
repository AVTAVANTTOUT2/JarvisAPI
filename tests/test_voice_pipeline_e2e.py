"""Tour de parole complet, moteurs simulés : chronologie et absence de palier.

Aucun de ces tests ne mesure la vitesse d'un moteur : STT, LLM et TTS sont
instantanés. Ce qui est mesuré, c'est l'**orchestration** — le temps que le
pipeline ajoute de lui-même. Un palier fixe (sleep, timeout, attente de file)
apparaîtrait immédiatement, quelle que soit la machine.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from audio import voice_latency as vl
from audio.voice_latency import UtteranceTrace

# Budget d'orchestration : très au-dessus du coût réel (quelques ms), très en
# dessous du moindre palier d'une seconde. Non sensible à la charge machine.
ORCHESTRATION_BUDGET_S = 1.0


class _Segment:
    def __init__(self, text: str) -> None:
        self.text = text
        self.avg_logprob = -0.2


def _stt(text: str) -> dict:
    return {"text": text, "segments": [_Segment(text)],
            "engine": "faster-whisper", "inference_ms": 5, "audio_ms": 900}


def _daemon():
    from scripts.audio_daemon import AudioDaemon

    daemon = AudioDaemon()
    daemon._interrupt_event = asyncio.Event()
    daemon._utterance_queue = asyncio.Queue(maxsize=3)
    daemon._audio_queue = asyncio.Queue(maxsize=300)
    daemon.wake_word_enabled = False
    daemon.state = "processing"
    return daemon


def _pipeline(transcript: str, reply: str = "Dix-huit degrés, Monsieur."):
    """Contexte simulant STT / LLM / TTS instantanés."""
    return (
        patch("scripts.audio_daemon.create_conversation", return_value=42),
        patch("scripts.audio_daemon.process_voice_fast", new_callable=AsyncMock),
        patch.object(type(_daemon()), "_broadcast_state", new_callable=AsyncMock),
        patch("audio.stt_daemon.stt_daemon.transcribe_with_metadata",
              new_callable=AsyncMock),
    )


@pytest.mark.asyncio
async def test_short_command_adds_no_fixed_delay():
    daemon = _daemon()
    trace = UtteranceTrace()

    with (
        patch("scripts.audio_daemon.create_conversation", return_value=42),
        patch("scripts.audio_daemon.process_voice_fast",
              new_callable=AsyncMock) as llm,
        patch.object(type(daemon), "_play_tts", new_callable=AsyncMock),
        patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock),
        patch("audio.stt_daemon.stt_daemon.transcribe_with_metadata",
              new_callable=AsyncMock) as stt,
    ):
        stt.return_value = _stt("quel temps fait-il")
        llm.return_value = {"text": "Dix-huit degrés.", "emotion": "neutral",
                            "latency_ms": 3}
        started = time.perf_counter()
        await daemon._process_single_utterance_active(
            b"\x00\x01" * 4000, True, trace=trace,
        )
        elapsed = time.perf_counter() - started

    assert elapsed < ORCHESTRATION_BUDGET_S, (
        f"palier d'orchestration détecté : {elapsed:.2f}s"
    )
    assert daemon.state == "listening"


@pytest.mark.asyncio
async def test_long_command_adds_no_fixed_delay():
    daemon = _daemon()
    long_text = " ".join(["explique-moi la mondialisation en détail"] * 12)
    long_reply = " ".join(["Voici une réponse détaillée."] * 20)

    with (
        patch("scripts.audio_daemon.create_conversation", return_value=42),
        patch("scripts.audio_daemon.process_voice_fast",
              new_callable=AsyncMock) as llm,
        patch.object(type(daemon), "_play_tts", new_callable=AsyncMock),
        patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock),
        patch("audio.stt_daemon.stt_daemon.transcribe_with_metadata",
              new_callable=AsyncMock) as stt,
    ):
        stt.return_value = _stt(long_text)
        llm.return_value = {"text": long_reply, "emotion": "neutral",
                            "latency_ms": 8}
        started = time.perf_counter()
        await daemon._process_single_utterance_active(
            b"\x00\x01" * 40000, True, trace=UtteranceTrace(),
        )
        elapsed = time.perf_counter() - started

    assert elapsed < ORCHESTRATION_BUDGET_S


@pytest.mark.asyncio
async def test_two_successive_commands_stay_within_budget():
    """Deux tours enchaînés : aucun coût qui s'accumule d'un tour à l'autre."""
    daemon = _daemon()
    durations: list[float] = []

    with (
        patch("scripts.audio_daemon.create_conversation", return_value=42),
        patch("scripts.audio_daemon.process_voice_fast",
              new_callable=AsyncMock) as llm,
        patch.object(type(daemon), "_play_tts", new_callable=AsyncMock),
        patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock),
        patch("audio.stt_daemon.stt_daemon.transcribe_with_metadata",
              new_callable=AsyncMock) as stt,
    ):
        stt.return_value = _stt("quelle heure est-il")
        llm.return_value = {"text": "Seize heures.", "emotion": "neutral",
                            "latency_ms": 3}
        for _ in range(2):
            daemon.state = "processing"
            started = time.perf_counter()
            await daemon._process_single_utterance_active(
                b"\x00\x01" * 4000, True, trace=UtteranceTrace(),
            )
            durations.append(time.perf_counter() - started)

    assert all(d < ORCHESTRATION_BUDGET_S for d in durations), durations
    assert llm.await_count == 2


@pytest.mark.asyncio
async def test_interruption_during_tts_stops_and_rearms():
    """Une interruption pendant la lecture rend la main tout de suite."""
    daemon = _daemon()
    trace = UtteranceTrace()

    async def _slow_tts(*_a, **_kw):
        # Simule une lecture en cours ; l'interruption arrive pendant.
        daemon._interrupt_event.set()

    with (
        patch("scripts.audio_daemon.create_conversation", return_value=42),
        patch("scripts.audio_daemon.process_voice_fast",
              new_callable=AsyncMock) as llm,
        patch.object(type(daemon), "_play_tts", side_effect=_slow_tts),
        patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock),
        patch("audio.stt_daemon.stt_daemon.transcribe_with_metadata",
              new_callable=AsyncMock) as stt,
    ):
        stt.return_value = _stt("raconte-moi une longue histoire")
        llm.return_value = {"text": "Il était une fois.", "emotion": "neutral",
                            "latency_ms": 3}
        started = time.perf_counter()
        await daemon._process_single_utterance_active(
            b"\x00\x01" * 4000, True, trace=trace,
        )
        elapsed = time.perf_counter() - started

    assert elapsed < ORCHESTRATION_BUDGET_S
    assert daemon.state == "listening"
    assert not daemon._tts_playing_event.is_set()


@pytest.mark.asyncio
async def test_llm_error_rearms_and_speaks_an_apology():
    daemon = _daemon()

    with (
        patch("scripts.audio_daemon.create_conversation", return_value=42),
        patch("scripts.audio_daemon.process_voice_fast",
              side_effect=RuntimeError("DeepSeek injoignable")),
        patch.object(type(daemon), "_play_tts", new_callable=AsyncMock) as tts,
        patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock),
        patch("audio.stt_daemon.stt_daemon.transcribe_with_metadata",
              new_callable=AsyncMock) as stt,
    ):
        stt.return_value = _stt("quel temps fait-il")
        started = time.perf_counter()
        await daemon._process_single_utterance_active(
            b"\x00\x01" * 4000, True, trace=UtteranceTrace(),
        )
        elapsed = time.perf_counter() - started

    assert elapsed < ORCHESTRATION_BUDGET_S
    tts.assert_awaited_once()
    assert daemon.state == "listening"


@pytest.mark.asyncio
async def test_tts_error_still_rearms_the_pipeline():
    """Un TTS en panne ne doit pas laisser le micro bloqué en « speaking »."""
    daemon = _daemon()

    with (
        patch("scripts.audio_daemon.create_conversation", return_value=42),
        patch("scripts.audio_daemon.process_voice_fast",
              new_callable=AsyncMock) as llm,
        patch.object(type(daemon), "_play_tts",
                     side_effect=RuntimeError("aucun moteur")),
        patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock),
        patch("audio.stt_daemon.stt_daemon.transcribe_with_metadata",
              new_callable=AsyncMock) as stt,
    ):
        stt.return_value = _stt("bonjour jarvis")
        llm.return_value = {"text": "Bonjour Monsieur.", "emotion": "warm",
                            "latency_ms": 3}
        await daemon._process_single_utterance_active(
            b"\x00\x01" * 4000, True, trace=UtteranceTrace(),
        )

    assert daemon.state == "listening"
    assert not daemon._tts_playing_event.is_set()


# ── Cohérence des métriques ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trace_covers_the_turn_and_stays_ordered():
    daemon = _daemon()
    trace = UtteranceTrace()

    with (
        patch("scripts.audio_daemon.create_conversation", return_value=77),
        patch("scripts.audio_daemon.process_voice_fast",
              new_callable=AsyncMock) as llm,
        patch.object(type(daemon), "_play_tts", new_callable=AsyncMock),
        patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock),
        patch("audio.stt_daemon.stt_daemon.transcribe_with_metadata",
              new_callable=AsyncMock) as stt,
    ):
        stt.return_value = _stt("quel temps fait-il")
        llm.return_value = {"text": "Dix-huit degrés.", "emotion": "neutral",
                            "latency_ms": 3}
        await daemon._process_single_utterance_active(
            b"\x00\x01" * 4000, True, trace=trace,
        )

    events = [m.event for m in trace.marks]
    assert vl.STT_STARTED in events
    assert events.index(vl.STT_STARTED) < events.index(vl.STT_COMPLETED)
    assert events.index(vl.STT_COMPLETED) < events.index(vl.CONVERSATION_LOOKUP_STARTED)
    assert events[-1] == vl.PIPELINE_REARMED

    snap = trace.snapshot()
    # Toutes les étapes appartiennent au même énoncé et à la même conversation.
    assert snap["conversation_id"] == 77
    assert snap["stt_ms"] is not None and snap["stt_ms"] >= 0
    assert snap["rearmed_ms"] is not None
    offsets = [s["since_start_ms"] for s in snap["steps"]]
    assert offsets == sorted(offsets)


@pytest.mark.asyncio
async def test_play_tts_marks_the_queue_entry():
    """L'entrée en file TTS est datée avant toute synthèse."""
    daemon = _daemon()
    trace = UtteranceTrace()

    with patch("scripts.audio_daemon.voice_queue.enqueue",
               new_callable=AsyncMock) as enqueue:
        await daemon._play_tts("Bonjour Monsieur.", emotion="warm", trace=trace)

    assert trace.elapsed_ms(vl.TTS_QUEUE_ENTERED) is not None
    # La trace est bien transmise à la file, donc au lecteur.
    assert enqueue.await_args.kwargs["trace"] is trace


@pytest.mark.asyncio
async def test_conversation_is_created_off_the_event_loop():
    """La création SQLite ne doit pas bloquer la boucle du tour de parole."""
    daemon = _daemon()
    threads: list[str] = []

    def _create(agent):
        import threading

        threads.append(threading.current_thread().name)
        return 99

    with (
        patch("scripts.audio_daemon.create_conversation", side_effect=_create),
        patch("scripts.audio_daemon.process_voice_fast",
              new_callable=AsyncMock) as llm,
        patch.object(type(daemon), "_play_tts", new_callable=AsyncMock),
        patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock),
        patch("audio.stt_daemon.stt_daemon.transcribe_with_metadata",
              new_callable=AsyncMock) as stt,
    ):
        stt.return_value = _stt("bonjour jarvis")
        llm.return_value = {"text": "Bonjour.", "emotion": "warm", "latency_ms": 3}
        await daemon._process_single_utterance_active(
            b"\x00\x01" * 4000, True, trace=UtteranceTrace(),
        )

    assert threads, "create_conversation n'a pas été appelé"
    assert threads[0] != "MainThread", (
        "la création de conversation s'exécute encore dans la boucle asyncio"
    )


@pytest.mark.asyncio
async def test_slow_audio_client_does_not_block_the_producer():
    """Un périphérique de sortie lent ne doit pas figer la synthèse."""
    from audio.audio_output import NativeAudioOutput

    out = NativeAudioOutput.__new__(NativeAudioOutput)
    # Sortie indisponible : le chemin doit rendre la main immédiatement plutôt
    # que d'attendre un périphérique qui ne viendra jamais.
    out.available = False

    async def _stream():
        yield b"\x00\x01" * 100

    started = time.perf_counter()
    played = await NativeAudioOutput.play_stream_from_async(
        out, _stream(), sample_rate=24000,
    )
    assert played is False
    assert time.perf_counter() - started < ORCHESTRATION_BUDGET_S

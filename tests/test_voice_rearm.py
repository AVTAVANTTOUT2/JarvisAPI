"""Réarmement du pipeline vocal après un tour avorté.

Scénario observé en production : une transcription vide laissait le daemon
inerte pendant ~29 secondes. Une parole non transcrite n'est pas une fin de
conversation ; le micro doit rester armé et le tour suivant être accepté
immédiatement, sans nouveau déclenchement du wake word.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


class _Segment:
    """Segment faster-whisper minimal : le filtre de confiance lit avg_logprob."""

    def __init__(self, text: str, avg_logprob: float = -0.25) -> None:
        self.text = text
        self.avg_logprob = avg_logprob


def _stt_result(text: str) -> dict:
    return {
        "text": text,
        "segments": [_Segment(text)] if text else [],
        "engine": "faster-whisper",
        "inference_ms": 120,
        "audio_ms": 900,
    }


def _daemon():
    """Daemon isolé : pas de micro, pas de moteur, pas de socket."""
    from scripts.audio_daemon import AudioDaemon

    daemon = AudioDaemon()
    daemon._interrupt_event = asyncio.Event()
    daemon._utterance_queue = asyncio.Queue(maxsize=3)
    daemon._audio_queue = asyncio.Queue(maxsize=300)
    daemon.state = "processing"
    return daemon


# ── Une transcription vide ne produit aucun effet de bord ───────────────────


@pytest.mark.asyncio
async def test_empty_transcription_creates_nothing_and_rearms():
    from audio.voice_latency import UtteranceTrace

    daemon = _daemon()
    daemon.wake_word_enabled = False
    trace = UtteranceTrace()

    with (
        patch("scripts.audio_daemon.create_conversation") as create_conv,
        patch("scripts.audio_daemon.save_message") as save_msg,
        patch("scripts.audio_daemon.process_voice_fast", new_callable=AsyncMock) as llm,
        patch.object(type(daemon), "_play_tts", new_callable=AsyncMock) as tts,
        patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock),
        patch("audio.stt_daemon.stt_daemon.transcribe_with_metadata",
              new_callable=AsyncMock) as stt,
    ):
        stt.return_value = _stt_result("")
        await daemon._process_single_utterance_active(
            b"\x00\x01" * 2000, True, trace=trace,
        )

    create_conv.assert_not_called()
    save_msg.assert_not_called()
    llm.assert_not_awaited()
    tts.assert_not_awaited()

    # Réarmé sur-le-champ, sans état résiduel.
    assert daemon.state == "listening"
    assert not daemon._interrupt_event.is_set()
    assert not daemon._tts_playing_event.is_set()
    assert trace.elapsed_ms("voice.pipeline.rearmed") is not None


@pytest.mark.asyncio
async def test_second_utterance_is_accepted_right_after_an_empty_one():
    """La parole qui suit une transcription vide n'attend aucun délai."""
    from audio.voice_latency import UtteranceTrace

    daemon = _daemon()
    daemon.wake_word_enabled = False
    transcripts = ["", "quel temps fait-il"]

    async def _fake_stt(*_a, **_kw):
        return _stt_result(transcripts.pop(0))

    with (
        patch("scripts.audio_daemon.create_conversation", return_value=408),
        patch("scripts.audio_daemon.process_voice_fast",
              new_callable=AsyncMock) as llm,
        patch.object(type(daemon), "_play_tts", new_callable=AsyncMock),
        patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock),
        patch("audio.stt_daemon.stt_daemon.transcribe_with_metadata", _fake_stt),
    ):
        llm.return_value = {"text": "Dix-huit degrés.", "emotion": "neutral",
                            "latency_ms": 12}

        await daemon._process_single_utterance_active(
            b"\x00\x01" * 2000, True, trace=UtteranceTrace(),
        )
        assert daemon.state == "listening"

        # Le tour suivant part immédiatement : aucune condition à lever.
        daemon.state = "processing"
        await daemon._process_single_utterance_active(
            b"\x00\x01" * 2000, True, trace=UtteranceTrace(),
        )

    llm.assert_awaited_once()
    assert daemon.state == "listening"


# ── Le wake word ne doit pas être réexigé au milieu d'une conversation ──────


@pytest.mark.asyncio
async def test_open_conversation_rearms_to_listening_even_with_wake_word():
    """C'est la cause du silence de ~29 s : retour en attente de wake word."""
    import time

    from audio.voice_latency import UtteranceTrace

    daemon = _daemon()
    daemon.wake_word_enabled = True
    daemon._conv_start_time = time.time()  # conversation en cours

    with (
        patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock),
        patch("audio.stt_daemon.stt_daemon.transcribe_with_metadata",
              new_callable=AsyncMock) as stt,
    ):
        stt.return_value = _stt_result("")
        await daemon._process_single_utterance_active(
            b"\x00\x01" * 2000, True, trace=UtteranceTrace(),
        )

    assert daemon.conversation_open() is True
    assert daemon.state == "listening"


@pytest.mark.asyncio
async def test_stale_conversation_returns_to_wake_word():
    """Hors conversation, le wake word reprend bien ses droits."""
    import time

    import config
    from audio.voice_latency import UtteranceTrace

    daemon = _daemon()
    daemon.wake_word_enabled = True
    timeout = float(getattr(config, "AUDIO_DAEMON_CONVERSATION_TIMEOUT", 45.0))
    daemon._conv_start_time = time.time() - (timeout + 10)

    with (
        patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock),
        patch("audio.stt_daemon.stt_daemon.transcribe_with_metadata",
              new_callable=AsyncMock) as stt,
    ):
        stt.return_value = _stt_result("")
        await daemon._process_single_utterance_active(
            b"\x00\x01" * 2000, True, trace=UtteranceTrace(),
        )

    assert daemon.conversation_open() is False
    assert daemon.state == "wake_listening"


@pytest.mark.asyncio
async def test_rearm_clears_interrupt_and_pending_utterances():
    daemon = _daemon()
    daemon.wake_word_enabled = False
    daemon._interrupt_event.set()
    daemon._tts_playing_event.set()
    daemon._utterance_queue.put_nowait((None, b"obsolete"))

    with patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock):
        await daemon._rearm(reason="test", purge_utterances=True)

    assert not daemon._interrupt_event.is_set()
    assert not daemon._tts_playing_event.is_set()
    assert daemon._utterance_queue.empty()
    assert daemon.state == "listening"


# ── Le tour complet réarme aussi, et la conversation reste ouverte ──────────


@pytest.mark.asyncio
async def test_completed_turn_keeps_conversation_open():
    from audio.voice_latency import UtteranceTrace

    daemon = _daemon()
    daemon.wake_word_enabled = True

    with (
        patch("scripts.audio_daemon.create_conversation", return_value=1),
        patch("scripts.audio_daemon.process_voice_fast",
              new_callable=AsyncMock) as llm,
        patch.object(type(daemon), "_play_tts", new_callable=AsyncMock),
        patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock),
        patch("audio.stt_daemon.stt_daemon.transcribe_with_metadata",
              new_callable=AsyncMock) as stt,
    ):
        stt.return_value = _stt_result("bonjour jarvis comment vas-tu")
        llm.return_value = {"text": "Très bien, Monsieur.", "emotion": "warm",
                            "latency_ms": 20}
        await daemon._process_single_utterance_active(
            b"\x00\x01" * 2000, True, trace=UtteranceTrace(),
        )

    # Après une réponse, l'utilisateur peut enchaîner sans redire « Jarvis ».
    assert daemon.state == "listening"
    assert daemon.conversation_open() is True

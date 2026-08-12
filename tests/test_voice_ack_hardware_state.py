"""Le repli qualité STT ne doit ni parler, ni laisser le micro coupé.

Trois défauts distincts se combinaient pour rendre le daemon sourd après un
simple bruit de fond. Ils sont figés ici séparément, parce qu'ils se corrigent
à trois endroits différents et qu'aucun ne suffit seul.

1. **Le déclencheur.** ``_needs_quality_fallback`` traitait « aucun indicateur »
   comme « probablement de la parole ». Or faster-whisper ne renvoie *aucun*
   segment sur du silence : les deux indicateurs valent alors ``None``, et
   ``(None or 0.0) < 0.5`` était vrai. Chaque bruit déclenchait donc la
   relecture par le modèle lourd — exactement ce que la docstring promettait
   d'éviter.

2. **La parole.** Cette relecture faisait entendre « Bien, Monsieur. » pour
   couvrir son temps de chargement. Sur du bruit, JARVIS répondait donc à
   personne. L'accusé anticipé générique a depuis été supprimé : le repli
   qualité n'émet plus qu'un signal d'observabilité.

3. **Le micro.** En half-duplex (le défaut), cet accusé coupait le flux
   d'entrée. Seul le chemin de fin de tour normal le rouvrait ; toutes les
   sorties anticipées — transcription vide, écho post-TTS, transcription
   rejetée — passent par ``_rearm``, qui ne le rouvrait pas. Le daemon restait
   alors muet et sourd jusqu'au prochain crash de la boucle. Le réarmement
   reste vérifié ici : il protège toutes les autres sorties anticipées.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


class _FakeStream:
    """Flux pyaudio minimal : seul l'état marche/arrêt nous intéresse."""

    def __init__(self) -> None:
        self.active = True
        self.starts = 0
        self.stops = 0

    def is_active(self) -> bool:
        return self.active

    def start_stream(self) -> None:
        self.active = True
        self.starts += 1

    def stop_stream(self) -> None:
        self.active = False
        self.stops += 1


def _daemon():
    from scripts.audio_daemon import AudioDaemon

    daemon = AudioDaemon()
    daemon._interrupt_event = asyncio.Event()
    daemon._utterance_queue = asyncio.Queue(maxsize=3)
    daemon._audio_queue = asyncio.Queue(maxsize=300)
    daemon.state = "processing"
    daemon.wake_word_enabled = False
    daemon._half_duplex = True
    daemon._running = True
    daemon._stream = _FakeStream()
    return daemon


# ── 1. Le déclencheur ────────────────────────────────────────────────────────


def test_silence_without_segments_never_asks_for_the_heavy_model():
    """Aucun segment = silence. C'est la forme la plus courante du silence.

    Le cas déjà couvert (`max_no_speech_prob=0.95`) suppose que le modèle a
    rendu un segment et l'a jugé non-parlé. Quand il ne rend rien du tout, il
    n'y a aucun indicateur — et c'est précisément le cas majoritaire.
    """
    from audio.stt_daemon import TranscriptionResult, _needs_quality_fallback

    silence = TranscriptionResult(
        text="",
        engine="faster-whisper",
        model="small",
        avg_logprob=None,
        max_no_speech_prob=None,
    )

    assert _needs_quality_fallback(silence) is False


def test_empty_transcript_with_evidence_of_speech_still_escalates():
    """Le garde ne doit pas devenir un « jamais » : la parole perdue escalade.

    Un segment présent, jugé parlé, mais sans texte décodé reste une anomalie
    qui mérite la relecture. On ne corrige que l'absence d'indicateur.
    """
    from audio.stt_daemon import TranscriptionResult, _needs_quality_fallback

    lost_speech = TranscriptionResult(
        text="",
        engine="faster-whisper",
        model="small",
        avg_logprob=-0.20,
        max_no_speech_prob=0.05,
    )

    assert _needs_quality_fallback(lost_speech) is True


@pytest.mark.asyncio
async def test_no_ack_when_the_quality_model_cache_is_incomplete(monkeypatch):
    """Ne pas parler pour couvrir un travail dont les poids sont absents.

    Les poids du modèle qualité peuvent être absents ou incomplets, et le
    runtime interdit tout téléchargement. La vérification légère doit arrêter
    le chemin avant le chargement et avant l'accusé.
    """
    from audio.stt_daemon import (
        FallbackSTTBackend,
        FasterWhisperBackend,
        TranscriptionResult,
    )

    primary = FasterWhisperBackend("small")
    quality = FasterWhisperBackend("large-v3-turbo")
    acks: list[str] = []

    async def _primary(*_args, **_kwargs):
        return TranscriptionResult(
            text="Quel temps fait Hilal Il ?",
            engine="faster-whisper",
            model="small",
            avg_logprob=-0.44,
            max_no_speech_prob=0.09,
        )

    async def _notice():
        acks.append("repli-qualite")

    monkeypatch.setattr(primary, "preload_sync", lambda: True)
    monkeypatch.setattr(quality, "is_available_locally", lambda: False)
    monkeypatch.setattr(
        quality,
        "preload_sync",
        lambda: pytest.fail("un cache incomplet ne doit jamais être chargé"),
    )
    monkeypatch.setattr(primary, "transcribe_pcm", _primary)
    backend = FallbackSTTBackend([primary, quality])

    result = await backend.transcribe_pcm_with_quality_callback(
        b"pcm" * 400,
        sample_rate=16000,
        on_quality_fallback=_notice,
    )

    assert result is not None
    assert result.quality_fallback_used is False
    assert result.text == "Quel temps fait Hilal Il ?"
    assert acks == [], (
        "aucun signal de repli ne doit être émis si la relecture n'a pas lieu"
    )


# ── 2 et 3. La parole et le micro ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rearm_restores_the_microphone_after_a_stopped_stream():
    """Toute sortie anticipée doit rendre le micro, pas seulement la fin de tour.

    ``_rearm`` promet « sans état résiduel ». Un flux d'entrée arrêté est un
    état résiduel : c'est celui qui rendait le daemon définitivement sourd.
    """
    daemon = _daemon()

    with patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock):
        daemon._stream.stop_stream()          # ce que fait le TTS en half-duplex
        assert daemon._stream.is_active() is False

        await daemon._rearm(reason="empty_transcript")

    assert daemon.state == "listening"
    assert daemon._stream.is_active() is True, (
        "le micro doit être rouvert par le réarmement, sinon le daemon est sourd"
    )


@pytest.mark.asyncio
async def test_noise_does_not_make_jarvis_speak_and_leaves_the_mic_open():
    """Bout en bout : du bruit ne produit ni parole, ni micro coupé.

    Le VAD laisse passer du bruit, le STT ne rend rien. Avant correction :
    « Bien, Monsieur. » était prononcé, le modèle lourd était chargé, et le
    micro restait fermé.
    """
    from audio.voice_latency import UtteranceTrace

    daemon = _daemon()
    trace = UtteranceTrace()
    spoken: list[str] = []

    async def _record_tts(self, text, **_kwargs):
        spoken.append(text)

    async def _stt(*_args, **kwargs):
        # Le daemon fournit le callback ; le backend ne doit pas l'appeler
        # pour du silence. On reproduit ici la décision réelle du backend.
        from audio.stt_daemon import TranscriptionResult, _needs_quality_fallback

        empty = TranscriptionResult(
            text="", engine="faster-whisper", model="small",
            avg_logprob=None, max_no_speech_prob=None,
        )
        callback = kwargs.get("on_quality_fallback")
        if callback is not None and _needs_quality_fallback(empty):
            await callback()
        return {"text": "", "segments": [], "engine": "faster-whisper",
                "inference_ms": 90, "audio_ms": 800}

    with (
        patch("scripts.audio_daemon.create_conversation") as create_conv,
        patch("scripts.audio_daemon.process_voice_fast", new_callable=AsyncMock) as llm,
        patch.object(type(daemon), "_play_tts", _record_tts),
        patch.object(type(daemon), "_broadcast_state", new_callable=AsyncMock),
        patch("audio.stt_daemon.stt_daemon.transcribe_with_metadata", _stt),
    ):
        await daemon._process_single_utterance_active(
            b"\x00\x01" * 2000, True, trace=trace,
        )
        await asyncio.sleep(0)  # laisse retomber une éventuelle tâche d'accusé

    create_conv.assert_not_called()
    llm.assert_not_awaited()
    assert spoken == [], f"JARVIS a parlé sur du bruit : {spoken}"
    assert daemon._stream.is_active() is True
    assert daemon.state == "listening"


@pytest.mark.asyncio
async def test_processing_signal_is_silent_and_never_resurrects_a_rearmed_state():
    """Le signal de début de tour est un état, pas une parole.

    Il remplace l'accusé anticipé. Deux propriétés à tenir : il ne synthétise
    rien, et s'il arrive après l'abandon du tour il ne doit pas réafficher
    « processing » alors que le daemon écoute déjà.
    """
    from audio.voice_latency import UtteranceTrace

    daemon = _daemon()
    spoken: list[str] = []
    payloads: list[dict] = []

    async def _record_tts(self, text, **_kwargs):
        spoken.append(text)

    async def _broadcast(self, payload=None):
        if isinstance(payload, dict):
            payloads.append(payload)

    with (
        patch.object(type(daemon), "_play_tts", _record_tts),
        patch.object(type(daemon), "_broadcast_state", _broadcast),
    ):
        await daemon._signal_processing_started(UtteranceTrace())
        assert daemon.state == "processing"
        assert spoken == [], "le signal de début de tour ne doit rien prononcer"
        assert payloads and payloads[-1]["type"] == "voice_processing_started"

        # Le tour est abandonné, puis un signal tardif arrive.
        await daemon._rearm(reason="empty_transcript")
        assert daemon.state == "listening"
        await daemon._signal_processing_started(UtteranceTrace())

    assert daemon.state == "listening", (
        "un signal tardif ne doit pas ramener l'état à « processing »"
    )
    assert spoken == []


@pytest.mark.asyncio
async def test_quality_fallback_signal_makes_no_sound():
    """Le repli qualité est observable, jamais audible."""
    daemon = _daemon()
    spoken: list[str] = []
    payloads: list[dict] = []

    async def _record_tts(self, text, **_kwargs):
        spoken.append(text)

    async def _broadcast(self, payload=None):
        if isinstance(payload, dict):
            payloads.append(payload)

    with (
        patch.object(type(daemon), "_play_tts", _record_tts),
        patch.object(type(daemon), "_broadcast_state", _broadcast),
    ):
        await daemon._signal_quality_fallback()

    assert spoken == []
    assert payloads == [{"type": "voice_quality_fallback"}]

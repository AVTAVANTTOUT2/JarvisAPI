"""Latence du pipeline vocal : instrumentation, réarmement, absence de délai fixe.

Ces tests tournent avec des moteurs simulés. Ils ne mesurent pas la vitesse du
matériel — ils vérifient que l'**orchestration** n'introduit ni attente fixe,
ni étape bloquante, ni état résiduel, et que la chronologie reste corrélée par
``utterance_id``.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from audio import voice_latency as vl
from audio.vad_utterance import VadUtteranceCollector, VadUtteranceConfig
from audio.voice_latency import UtteranceTrace


# ── Instrumentation ─────────────────────────────────────────────────────────


def test_trace_marks_are_monotonic_and_correlated():
    trace = UtteranceTrace(conversation_id=7)
    trace.mark(vl.STT_STARTED, engine="faster-whisper", audio_ms=800)
    trace.mark(vl.STT_COMPLETED, engine="faster-whisper", text_chars=12)
    trace.mark(vl.TTS_PLAYBACK_STARTED, engine="kokoro")

    snap = trace.snapshot()
    assert snap["conversation_id"] == 7
    assert len(snap["utterance_id"]) == 12
    # Chaque étape porte le même identifiant implicite : elles vivent dans la
    # même trace, et les durées ne reculent jamais.
    offsets = [step["since_start_ms"] for step in snap["steps"]]
    assert offsets == sorted(offsets)
    assert all(step["since_previous_ms"] >= 0 for step in snap["steps"])
    assert snap["end_of_speech_to_first_audio_ms"] is not None


def test_trace_never_logs_content():
    """Aucun champ hors allowlist ne franchit la frontière d'instrumentation."""
    trace = UtteranceTrace()
    mark = trace.mark(
        vl.STT_COMPLETED,
        text_chars=42,
        transcript="mon code bancaire est 1234",
        api_key="sk-secret",
    )
    assert "transcript" not in mark.fields
    assert "api_key" not in mark.fields
    assert mark.fields["text_chars"] == 42


def test_every_required_step_name_exists():
    """Les étapes exigées par le cahier des charges sont toutes déclarées."""
    required = {
        "voice.segment.speech_started", "voice.segment.speech_ended",
        "voice.segment.finalized", "stt.queue.entered", "stt.started",
        "stt.completed", "conversation.lookup.started",
        "conversation.lookup.completed", "user_message.persist.started",
        "user_message.persist.completed", "context.build.started",
        "context.build.completed", "llm.queue.entered", "llm.request.started",
        "llm.first_token", "llm.completed",
        "assistant_message.persist.started", "assistant_message.persist.completed",
        "tts.queue.entered", "tts.model.ready", "tts.synthesis.started",
        "tts.first_audio_chunk", "tts.playback.started",
        "tts.synthesis.completed", "tts.playback.completed",
        "voice.pipeline.rearmed",
    }
    assert required <= vl.KNOWN_EVENTS


def test_span_and_elapsed_return_none_when_step_missing():
    trace = UtteranceTrace()
    trace.mark(vl.STT_STARTED)
    assert trace.span_ms(vl.STT_STARTED, vl.STT_COMPLETED) is None
    assert trace.elapsed_ms(vl.TTS_PLAYBACK_STARTED) is None
    assert trace.snapshot()["end_of_speech_to_first_audio_ms"] is None


# ── VAD : fin de parole et horodatage ───────────────────────────────────────


def _collector(**overrides) -> VadUtteranceCollector:
    cfg = VadUtteranceConfig(
        chunk_ms=30, silence_ms=90, min_speech_ms=30, pre_roll_ms=60,
        speech_threshold=0.02, **overrides,
    )
    state = {"speech": True}
    return VadUtteranceCollector(
        config=cfg, is_speech_fn=lambda _c: state["speech"],
    ), state


def test_vad_reports_speech_end_before_the_detection_silence():
    """La fin de parole exclut le silence qui a servi à la détecter.

    Sinon la mesure « fin de parole → premier son » facturerait à la réponse un
    délai qui appartient au VAD, et l'optimisation viserait la mauvaise étape.
    """
    collector, state = _collector()
    chunk = b"\x00\x10" * 480

    for _ in range(4):
        assert collector.ingest(chunk) is None
    state["speech"] = False
    finalize_start = time.perf_counter()
    audio = None
    for _ in range(3):
        audio = collector.ingest(chunk)
    assert audio is not None

    # 3 chunks de silence à 30 ms = 90 ms retranchés.
    assert collector.last_speech_ended_at is not None
    assert collector.last_speech_ended_at <= finalize_start
    assert collector.last_speech_started_at is not None
    # Jamais avant le début de la parole, même si les chunks sont rejoués plus
    # vite que le temps réel : une latence négative en aval serait absurde.
    assert collector.last_speech_started_at <= collector.last_speech_ended_at
    assert collector.last_audio_ms > 0


def test_vad_resets_speech_timestamp_between_utterances():
    collector, state = _collector()
    chunk = b"\x00\x10" * 480
    collector.ingest(chunk)
    assert collector.speech_started_at is not None
    collector.reset()
    assert collector.speech_started_at is None


# ── Découpage TTS : le premier fragment part tôt ────────────────────────────


def test_first_tts_fragment_is_short_so_audio_starts_early():
    from native_audio.kokoro_mlx import split_text_for_kokoro

    text = (
        "Bonjour Monsieur. Il fait dix-huit degrés à Lille avec un ciel couvert. "
        "Prenez un parapluie pour cet après-midi, la pluie est annoncée vers seize heures."
    )
    parts = split_text_for_kokoro(text, max_tokens=180, first_chunk_max_tokens=6)
    assert len(parts) > 1
    assert len(parts[0].split()) <= 6
    # Aucun mot perdu ni dupliqué par le découpage.
    assert " ".join(parts).split() == text.split()


def test_split_without_first_chunk_limit_keeps_historical_behaviour():
    from native_audio.kokoro_mlx import chunk_text_for_kokoro

    text = "Une phrase. Deux phrases. Trois phrases."
    assert chunk_text_for_kokoro(text, max_tokens=180) == text


def test_split_handles_empty_and_single_word():
    from native_audio.kokoro_mlx import split_text_for_kokoro

    assert split_text_for_kokoro("") == []
    assert split_text_for_kokoro("   ") == []
    assert split_text_for_kokoro("Oui.") == ["Oui."]


# ── Protocole du sidecar chaud ──────────────────────────────────────────────


def test_kokoro_frame_roundtrip():
    from native_audio.kokoro_mlx import FRAME_HEADER, TAG_CHUNK, encode_frame

    payload = b"\x01\x02\x03\x04"
    frame = encode_frame(TAG_CHUNK, payload)
    tag, length = FRAME_HEADER.unpack(frame[: FRAME_HEADER.size])
    assert tag == TAG_CHUNK
    assert length == len(payload)
    assert frame[FRAME_HEADER.size:] == payload


def test_kokoro_server_streams_one_frame_per_fragment():
    """Le serveur émet un fragment à la fois, pas un bloc final unique."""
    from native_audio.kokoro_mlx import KokoroServer

    class _FakeResult:
        def __init__(self, n: int) -> None:
            self.audio = [0.0] * n
            self.sample_rate = 24000

    class _FakeModel:
        def generate(self, *, text, voice, speed, lang_code):
            yield _FakeResult(len(text))

    server = KokoroServer("modele-simule")
    server._model = _FakeModel()

    chunks = list(server.synthesize_chunks({
        "text": "Bonjour Monsieur. Il fait beau aujourd'hui à Lille.",
        "first_chunk_max_tokens": 2,
        "max_tokens": 180,
    }))
    assert len(chunks) >= 2
    assert all(isinstance(c, bytes) and c for c in chunks)


# ── Fast path des interpellations triviales ─────────────────────────────────


@pytest.mark.parametrize("phrase", [
    "Jarvis",
    "jarvis ?",
    "JARVIS. JARVIS. JARVIS. JARVIS.",
    "Allô Jarvis",
    "Tu m'entends ?",
    "Jarvis, tu m'entends ?",
    "hey jarvis",
])
def test_trivial_hail_is_recognized(phrase):
    from api.voice_processing import match_trivial_hail

    assert match_trivial_hail(phrase) == "Je vous écoute, Monsieur."


@pytest.mark.parametrize("phrase", [
    "Jarvis quel temps fait-il ?",
    "Jarvis, éteins la télé",
    "ok",
    "",
    "Jarvis lance la commande git status",
    "Tu m'entends quand je te demande la météo ?",
])
def test_real_requests_are_not_short_circuited(phrase):
    """Le fast path ne doit jamais avaler une demande réelle."""
    from api.voice_processing import match_trivial_hail

    assert match_trivial_hail(phrase) is None


# ── Aucune attente fixe dans l'orchestration ────────────────────────────────


@pytest.mark.asyncio
async def test_voice_queue_dispatches_without_fixed_delay():
    """Un énoncé enfilé part immédiatement, sans palier d'attente."""
    from audio.voice_queue import VoicePriority, VoiceQueue

    queue = VoiceQueue()
    played = asyncio.Event()
    seen: dict = {}

    async def _play(text, emotion, cancel, *, trace=None):
        seen["text"] = text
        seen["trace"] = trace
        played.set()

    await queue.start(_play)
    try:
        started = time.perf_counter()
        trace = UtteranceTrace()
        await queue.enqueue(
            "Bonjour", priority=VoicePriority.USER_RESPONSE, trace=trace,
        )
        await asyncio.wait_for(played.wait(), timeout=2.0)
        elapsed = time.perf_counter() - started
    finally:
        await queue.stop()

    assert seen["text"] == "Bonjour"
    # La trace traverse bien la file jusqu'au lecteur.
    assert seen["trace"] is trace
    assert elapsed < 1.0, f"palier d'attente détecté : {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_llm_first_token_is_marked_before_completion(monkeypatch):
    """``llm.first_token`` doit précéder ``llm.completed``, pas le doubler."""
    import llm as llm_module

    async def _fake_stream_collect(*, on_first_token=None, **_kw):
        await asyncio.sleep(0)
        if on_first_token:
            on_first_token()
        await asyncio.sleep(0.02)
        return {"content": "Bonjour Monsieur.", "tokens_in": 10,
                "tokens_out": 4, "cost": 0.0, "model": "flash"}

    monkeypatch.setattr(llm_module, "chat_stream_collect", _fake_stream_collect)

    from api.voice_processing import _voice_llm_call

    trace = UtteranceTrace()
    trace.mark(vl.LLM_REQUEST_STARTED)
    result = await _voice_llm_call(
        messages=[{"role": "user", "content": "bonjour"}],
        system="s", max_tokens=250, temperature=0.5, trace=trace,
    )
    trace.mark(vl.LLM_COMPLETED)

    assert result["content"] == "Bonjour Monsieur."
    first = trace.elapsed_ms(vl.LLM_FIRST_TOKEN)
    done = trace.elapsed_ms(vl.LLM_COMPLETED)
    assert first is not None and done is not None
    assert first <= done


@pytest.mark.asyncio
async def test_llm_falls_back_to_buffered_call_when_stream_fails(monkeypatch):
    import llm as llm_module

    async def _boom(**_kw):
        raise RuntimeError("flux coupé")

    async def _buffered(**_kw):
        return {"content": "repli", "tokens_in": 1, "tokens_out": 1,
                "cost": 0.0, "model": "flash"}

    monkeypatch.setattr(llm_module, "chat_stream_collect", _boom)
    monkeypatch.setattr(llm_module, "chat", _buffered)

    from api.voice_processing import _voice_llm_call

    result = await _voice_llm_call(
        messages=[{"role": "user", "content": "bonjour"}],
        system="s", max_tokens=250, temperature=0.5, trace=None,
    )
    assert result["content"] == "repli"


# ── Réglages STT temps réel ─────────────────────────────────────────────────


def test_stt_realtime_defaults_are_measured_not_assumed():
    """`auto` choisit int8, deux fois plus lent ici — le défaut doit être explicite.

    Mesuré sur Apple Silicon (large-v3-turbo, 2,66 s de parole FR, CPU) :
    auto/int8 = 4609 ms, float32 = 2361 ms. CTranslate2 n'a pas de noyau int8
    accéléré sur ce CPU : la quantification ajoute une déquantification par
    couche au lieu d'économiser du calcul. Ce test empêche un retour silencieux
    à `auto`, qui doublerait la transcription.
    """
    import config

    assert config.DEFAULT_STT_COMPUTE_TYPE == "float32"
    assert config.DEFAULT_STT_BEAM_SIZE == 1
    assert config.DEFAULT_STT_VAD_FILTER is False


def test_stt_backend_uses_the_realtime_settings(monkeypatch):
    """Les réglages traversent bien jusqu'à l'appel du moteur."""
    import config
    from audio.stt_daemon import FasterWhisperBackend

    monkeypatch.setattr(config, "STT_BEAM_SIZE", 1, raising=False)
    monkeypatch.setattr(config, "STT_VAD_FILTER", False, raising=False)

    captured: dict = {}

    class _Info:
        language = "fr"
        duration = 1.0

    class _Model:
        def transcribe(self, _audio, **kwargs):
            captured.update(kwargs)
            return iter(()), _Info()

    backend = FasterWhisperBackend("large-v3-turbo")
    backend._model = _Model()
    backend._loaded = True

    result = asyncio.run(
        backend.transcribe_pcm(b"\x00\x01" * 2000, sample_rate=16000, language="fr")
    )

    assert captured["beam_size"] == 1
    assert captured["vad_filter"] is False
    assert captured["condition_on_previous_text"] is False
    # Le facteur temps réel est calculé, pas deviné.
    assert result is not None
    assert result.audio_ms > 0
    assert result.real_time_factor is not None


# ── Réglages versionnés, pas seulement locaux ───────────────────────────────


def test_latency_settings_are_versioned_defaults():
    """Une installation neuve doit hériter des valeurs mesurées.

    Ces réglages vivaient dans un `.env.config` gitignoré : un poste neuf
    repartait donc avec 1200 ms de silence et un décodage int8. Les défauts
    intégrés sont désormais la source de vérité.
    """
    import config

    assert config.DEFAULT_AUDIO_DAEMON_SILENCE_MS == 500
    assert config.DEFAULT_AUDIO_DAEMON_MIN_SPEECH_MS == 200
    assert config.DEFAULT_AUDIO_DAEMON_PRE_ROLL_MS == 300
    assert config.DEFAULT_STT_COMPUTE_TYPE == "float32"
    assert config.DEFAULT_STT_BEAM_SIZE == 1
    assert config.DEFAULT_STT_VAD_FILTER is False


def test_env_examples_agree_with_builtin_defaults():
    """Un exemple qui contredit le défaut intégré désinforme plus qu'il n'aide."""
    from pathlib import Path

    import config

    expected = {
        "AUDIO_DAEMON_SILENCE_MS": str(config.DEFAULT_AUDIO_DAEMON_SILENCE_MS),
        "AUDIO_DAEMON_MIN_SPEECH_MS": str(config.DEFAULT_AUDIO_DAEMON_MIN_SPEECH_MS),
        "AUDIO_DAEMON_PRE_ROLL_MS": str(config.DEFAULT_AUDIO_DAEMON_PRE_ROLL_MS),
        "STT_COMPUTE_TYPE": config.DEFAULT_STT_COMPUTE_TYPE,
        "STT_BEAM_SIZE": str(config.DEFAULT_STT_BEAM_SIZE),
        "STT_VAD_FILTER": str(config.DEFAULT_STT_VAD_FILTER).lower(),
    }
    root = Path(__file__).resolve().parent.parent

    for name in (".env.example", ".env.config.example"):
        lines = (root / name).read_text(encoding="utf-8").splitlines()
        for key, want in expected.items():
            hits = [ln for ln in lines if ln.startswith(f"{key}=")]
            assert len(hits) == 1, (
                f"{name} : {key} apparaît {len(hits)} fois — "
                "un doublon rend la valeur effective imprévisible."
            )
            got = hits[0].split("=", 1)[1].split("#")[0].strip()
            assert got == want, f"{name} : {key}={got}, attendu {want}"


def test_engine_config_exposes_the_realtime_settings():
    """Les réglages appliqués doivent être lisibles au démarrage et en diagnostic."""
    from audio.engine_config import load_audio_engine_config

    cfg = load_audio_engine_config()
    assert cfg.stt_beam_size >= 1
    assert cfg.stt_vad_filter is False
    assert cfg.vad_silence_ms > 0
    assert cfg.vad_min_speech_ms > 0
    assert cfg.vad_pre_roll_ms > 0

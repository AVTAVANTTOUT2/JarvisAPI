"""Tests pipeline audio natif macOS — VAD, STT, TTS, file vocale, ScreenWatcher."""

from __future__ import annotations

import asyncio
import struct
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from audio.resample import resample_pcm16_mono
from audio.vad_utterance import VadUtteranceCollector, VadUtteranceConfig, chunk_rms
from audio.voice_queue import VoicePriority, VoiceQueue


def _pcm_chunk(samples: int = 480, value: int = 8000) -> bytes:
    return struct.pack(f"<{samples}h", *([value] * samples))


def test_silence_does_not_grow_frames() -> None:
    """Plusieurs minutes de silence ne doivent pas remplir frames."""
    cfg = VadUtteranceConfig(chunk_ms=30, pre_roll_ms=300, silence_ms=450, min_speech_ms=200)
    collector = VadUtteranceCollector(
        config=cfg,
        is_speech_fn=lambda _: False,
    )
    silent_chunk = _pcm_chunk(value=50)

    for _ in range(10_000):  # ~5 min à 30 ms/chunk
        assert collector.ingest(silent_chunk) is None

    assert collector.frames == []
    assert len(collector.pre_speech_ring) <= collector._pre_roll_max


def test_pre_roll_not_duplicated_on_speech_start() -> None:
    """Le pré-roll est injecté une seule fois au début de la parole."""
    cfg = VadUtteranceConfig(chunk_ms=30, pre_roll_ms=300, silence_ms=450, min_speech_ms=200)
    collector = VadUtteranceCollector(
        config=cfg,
        is_speech_fn=lambda c: chunk_rms(c) > 0.02,
    )
    silence = _pcm_chunk(value=50)
    speech = _pcm_chunk(value=12000)

    for _ in range(5):
        collector.ingest(silence)
    assert collector.frames == []
    pre_roll_len = len(collector.pre_speech_ring)

    collector.ingest(speech)
    assert collector.has_speech
    # Une seule copie du ring : pas de duplication (bug historique = 2× pre_roll)
    assert len(collector.frames) == pre_roll_len + 1
    assert len(collector.frames) < (pre_roll_len + 1) * 2


def test_silence_ms_config_respected() -> None:
    """La fin de phrase respecte AUDIO_DAEMON_SILENCE_MS via silence_chunks."""
    silence_ms = 450
    chunk_ms = 30
    cfg = VadUtteranceConfig(
        chunk_ms=chunk_ms,
        silence_ms=silence_ms,
        min_speech_ms=200,
        pre_roll_ms=300,
    )
    collector = VadUtteranceCollector(
        config=cfg,
        is_speech_fn=lambda chunk: chunk_rms(chunk) > 0.02,
    )

    speech = _pcm_chunk(value=12000)
    silence = _pcm_chunk(value=40)

    for _ in range(8):
        collector.ingest(speech)

    result = None
    silence_needed = int(silence_ms / chunk_ms)
    for i in range(silence_needed + 5):
        result = collector.ingest(silence)
        if result:
            break

    assert result is not None
    assert i + 1 >= silence_needed


def test_single_word_command_accepted() -> None:
    from scripts.audio_daemon import ALLOWED_SHORT_COMMANDS, _is_acceptable_transcript

    for word in ("stop", "oui", "non", "merci", "silence", "annule", "continue"):
        assert word in ALLOWED_SHORT_COMMANDS
        assert _is_acceptable_transcript(word, used_local_stt=False, segments=[])


def test_native_play_tts_does_not_call_edge() -> None:
    """_play_tts_native ne doit jamais invoquer un service TTS réseau."""
    from scripts.audio_daemon import AudioDaemon

    daemon = AudioDaemon()
    mock_engine = MagicMock()
    mock_engine.get_backend_name.return_value = "macos"
    mock_engine.synthesize_native = AsyncMock(return_value=b"RIFF" + b"\x00" * 200)

    with (
        patch("scripts.audio_daemon.get_native_tts_engine", return_value=mock_engine),
        patch("scripts.audio_daemon.native_audio_output.available", True),
        patch("scripts.audio_daemon.native_audio_output.play_bytes", new_callable=AsyncMock) as play,
        patch("audio.tts.get_tts_by_name") as edge_get,
        patch("audio.tts.tts.synthesize", new_callable=AsyncMock) as edge_synth,
    ):
        asyncio.run(daemon._play_tts_native("Bonjour Monsieur.", emotion="neutral"))
        edge_get.assert_not_called()
        edge_synth.assert_not_called()
        play.assert_called_once()


@pytest.mark.asyncio
async def test_voice_queue_defers_background_during_user_turn() -> None:
    q = VoiceQueue()
    played: list[str] = []

    async def _play(text: str, emotion: str, cancel) -> None:
        played.append(text)

    await q.start(_play)
    q.set_user_conversation_active(True)

    ok = await q.enqueue("Notification écran", priority=VoicePriority.BACKGROUND)
    assert ok is True
    await asyncio.sleep(0.15)
    assert played == []

    ok_user = await q.enqueue("Réponse", priority=VoicePriority.USER_RESPONSE, wait=True)
    assert ok_user is True
    assert played == ["Réponse"]
    q.set_user_conversation_active(False)
    await asyncio.sleep(0.2)
    assert played == ["Réponse", "Notification écran"]
    await q.stop()


@pytest.mark.asyncio
async def test_voice_queue_critical_passes_during_conversation() -> None:
    q = VoiceQueue()

    async def _play(text: str, emotion: str, cancel) -> None:
        pass

    await q.start(_play)
    q.set_user_conversation_active(True)
    assert await q.enqueue("Urgence", priority=VoicePriority.CRITICAL) is True
    await q.stop()


def test_screen_watcher_defers_when_voice_busy() -> None:
    from scripts.screen_watcher import ScreenWatcher

    sw = ScreenWatcher()
    with patch.object(ScreenWatcher, "_is_voice_busy", return_value=True):
        assert asyncio.run(sw._analyze_with_ollama(MagicMock(), "Safari", {})) is None


def test_stt_engine_missing_uses_local_fallback(monkeypatch) -> None:
    monkeypatch.setattr("config.AUDIO_DAEMON_STT_ENGINE", "whisperkit")
    from audio.stt_daemon import (
        FasterWhisperBackend,
        FallbackSTTBackend,
        WhisperCppBackend,
        WhisperKitBackend,
        create_daemon_stt_backend,
    )

    with (
        patch.object(WhisperKitBackend, "preload_sync", return_value=False),
        patch.object(WhisperCppBackend, "preload_sync", return_value=False),
        patch.object(FasterWhisperBackend, "preload_sync", return_value=True),
    ):
        backend = create_daemon_stt_backend()
        assert isinstance(backend, FallbackSTTBackend)
        assert backend.preload_sync() is True
        assert backend.name == "faster-whisper"


def test_stt_all_local_engines_missing_fails_cleanly(monkeypatch) -> None:
    monkeypatch.setattr("config.AUDIO_DAEMON_STT_ENGINE", "whisperkit")
    from audio.stt_daemon import (
        FasterWhisperBackend,
        WhisperCppBackend,
        WhisperKitBackend,
        create_daemon_stt_backend,
    )

    with (
        patch.object(WhisperKitBackend, "preload_sync", return_value=False),
        patch.object(WhisperCppBackend, "preload_sync", return_value=False),
        patch.object(FasterWhisperBackend, "preload_sync", return_value=False),
    ):
        assert create_daemon_stt_backend().preload_sync() is False


def test_collector_reset_after_exception_path() -> None:
    """reset() vide tous les buffers (simule fin / erreur)."""
    cfg = VadUtteranceConfig(chunk_ms=30, silence_ms=300, min_speech_ms=100, pre_roll_ms=200)
    collector = VadUtteranceCollector(
        config=cfg,
        is_speech_fn=lambda c: chunk_rms(c) > 0.02,
    )
    collector.ingest(_pcm_chunk(value=10000))
    collector.reset()
    assert collector.frames == []
    assert collector.has_speech is False
    assert len(collector.pre_speech_ring) == 0


def test_jarvis_daemon_tts_relay_uses_voice_queue() -> None:
    """_tts_loop relaie vers voice_queue sans appeler Edge."""
    from scripts.jarvis_daemon import JarvisDaemon

    d = JarvisDaemon()
    d.running = True
    d.mode = "conversation"
    d.tts_queue.put_nowait("Test notification")

    async def _run_once() -> None:
        with patch("scripts.jarvis_daemon.voice_queue.enqueue", new_callable=AsyncMock) as enq:
            task = asyncio.create_task(d._tts_loop())
            await asyncio.sleep(0.2)
            d.running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert enq.called

    asyncio.run(_run_once())


def test_single_mic_no_second_porcupine_thread() -> None:
    """_start_wake_detection ne lance plus de 2e flux Porcupine."""
    from scripts.audio_daemon import AudioDaemon

    daemon = AudioDaemon()
    loop = asyncio.new_event_loop()
    with patch.object(loop, "run_in_executor") as run_exec:
        daemon._start_wake_detection(loop)
        run_exec.assert_not_called()
    loop.close()


@pytest.mark.asyncio
async def test_voice_queue_all_tts_through_consumer() -> None:
    played: list[str] = []
    q = VoiceQueue()

    async def _play(text: str, emotion: str, cancel) -> None:
        played.append(text)

    await q.start(_play)
    await q.enqueue("A", priority=VoicePriority.BACKGROUND)
    await asyncio.sleep(0.15)
    assert played == ["A"]
    await q.stop()


def test_collector_reset_after_utterance() -> None:
    cfg = VadUtteranceConfig(chunk_ms=30, silence_ms=300, min_speech_ms=100, pre_roll_ms=200)
    collector = VadUtteranceCollector(
        config=cfg,
        is_speech_fn=lambda c: chunk_rms(c) > 0.02,
    )
    speech = _pcm_chunk(value=10000)
    silence = _pcm_chunk(value=30)
    for _ in range(4):
        collector.ingest(speech)
    out = None
    for _ in range(20):
        out = collector.ingest(silence)
        if out:
            break
    assert out is not None
    assert collector.frames == []
    assert collector.has_speech is False


def test_resample_48k_to_16k_once() -> None:
    pcm48 = _pcm_chunk(samples=480, value=5000)
    out = resample_pcm16_mono(pcm48, 48000, 16000)
    assert len(out) == 160 * 2  # 480/3 samples


@pytest.mark.asyncio
async def test_audio_daemon_stop_preserves_notification_output() -> None:
    from scripts.audio_daemon import AudioDaemon
    from audio.voice_queue import voice_queue

    daemon = AudioDaemon()
    daemon.enabled = True
    daemon._running = False
    daemon._stop_event = asyncio.Event()

    async def _noop(*_a, **_k):
        pass

    await voice_queue.start(_noop)
    with patch("scripts.audio_daemon.config.DAEMON_ENABLED", True):
        await daemon.stop()
    assert voice_queue._consumer_task is not None
    assert not voice_queue._consumer_task.done()
    await voice_queue.stop()


def test_short_noise_does_not_satisfy_min_speech_with_pre_roll() -> None:
    cfg = VadUtteranceConfig(
        chunk_ms=30, silence_ms=90, min_speech_ms=200, pre_roll_ms=300,
    )
    collector = VadUtteranceCollector(
        config=cfg,
        is_speech_fn=lambda chunk: chunk_rms(chunk) > 0.02,
    )
    silence = _pcm_chunk(value=30)
    speech = _pcm_chunk(value=12000)
    for _ in range(10):
        collector.ingest(silence)
    collector.ingest(speech)
    assert collector.speech_chunks == 1
    for _ in range(3):
        assert collector.ingest(silence) is None
    assert collector.has_speech is False
    assert collector.frames == []


@pytest.mark.asyncio
async def test_passive_micro_does_not_block_screen_watcher() -> None:
    from audio.voice_queue import voice_queue
    from scripts.screen_watcher import ScreenWatcher

    await voice_queue.stop()
    voice_queue.set_user_conversation_active(False)
    voice_queue.set_mic_capture_active(True)
    assert ScreenWatcher._is_voice_busy() is False
    voice_queue.set_mic_capture_active(False)


@pytest.mark.asyncio
async def test_conversation_lock_released_on_early_return() -> None:
    from audio.voice_queue import voice_queue
    from scripts.audio_daemon import AudioDaemon

    daemon = AudioDaemon()
    daemon._interrupt_event = asyncio.Event()
    with (
        patch(
            "audio.stt_local.stt_local.transcribe_with_metadata",
            new_callable=AsyncMock,
            return_value={"text": "silence", "segments": [], "engine": "test"},
        ),
        patch.object(AudioDaemon, "_check_sleep_wake", return_value=True),
        patch.object(AudioDaemon, "_broadcast_state", new_callable=AsyncMock),
    ):
        await daemon._process_single_utterance(_pcm_chunk(), stt_available=True)
    assert voice_queue.user_conversation_active is False


@pytest.mark.asyncio
async def test_undecodable_native_audio_falls_back_to_afplay() -> None:
    from scripts.audio_daemon import AudioDaemon

    daemon = AudioDaemon()
    fake_proc = MagicMock(returncode=0)
    with (
        patch("scripts.audio_daemon.native_audio_output.available", True),
        patch(
            "scripts.audio_daemon.native_audio_output.play_bytes",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "scripts.audio_daemon.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=fake_proc,
        ) as create_proc,
        patch("scripts.audio_daemon._wait_subprocess", new_callable=AsyncMock),
    ):
        await daemon._play_audio_local(b"not-a-soundfile-container")
    create_proc.assert_awaited_once()


@pytest.mark.asyncio
async def test_jarvis_daemon_starts_voice_output_without_micro() -> None:
    from scripts.jarvis_daemon import JarvisDaemon

    daemon = JarvisDaemon()
    with patch("scripts.jarvis_daemon.voice_queue.start", new_callable=AsyncMock) as start:
        await daemon._ensure_voice_output()
    start.assert_awaited_once()


@pytest.mark.asyncio
async def test_priority_notification_spoken_when_micro_daemon_disabled() -> None:
    from audio.voice_queue import voice_queue
    from jarvis.event_bus import JarvisEvent
    from scripts.audio_daemon import (
        AudioDaemon,
        _speak_priority_notification,
        audio_daemon,
    )

    async def _noop(*_a, **_k):
        pass

    await voice_queue.start(_noop)
    audio_daemon.enabled = False
    with patch.object(AudioDaemon, "_play_tts", new_callable=AsyncMock) as speak:
        await _speak_priority_notification(JarvisEvent(
            type="notification.created",
            data={"priority": "high", "title": "Test", "content": "Local"},
        ))
    speak.assert_awaited_once_with("Test. Local", emotion="alert")
    await voice_queue.stop()


@pytest.mark.asyncio
async def test_critical_voice_request_cancels_current_playback() -> None:
    q = VoiceQueue()
    played: list[str] = []
    cancelled = asyncio.Event()

    async def _play(text: str, emotion: str, cancel) -> None:
        played.append(text)
        if text == "Fond":
            await cancel.wait()
            cancelled.set()

    await q.start(_play)
    await q.enqueue("Fond", priority=VoicePriority.BACKGROUND)
    await asyncio.sleep(0.05)
    await q.enqueue("Urgence", priority=VoicePriority.CRITICAL)
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
    await asyncio.sleep(0.15)
    assert played == ["Fond", "Urgence"]
    await q.stop()


@pytest.mark.asyncio
async def test_ttskit_stream_yields_pcm_incrementally() -> None:
    from audio.tts_native import TTSKitEngine

    first_released = asyncio.Event()

    async def _fake_stream(*_a, **_k):
        first_released.set()
        yield b"first"
        await asyncio.sleep(0)
        yield b"second"

    fake_bus = MagicMock()
    fake_bus.emit = AsyncMock()
    with (
        patch("native_audio.ttskit_bridge.is_ttskit_available", return_value=True),
        patch("native_audio.ttskit_bridge.stream_pcm16", new=_fake_stream),
        patch("audio.tts_native.event_bus", fake_bus),
    ):
        stream = TTSKitEngine().synthesize_stream("Bonjour Monsieur.")
        assert await anext(stream) == b"first"
        assert first_released.is_set()
        assert await anext(stream) == b"second"
        with pytest.raises(StopAsyncIteration):
            await anext(stream)


@pytest.mark.asyncio
async def test_screen_ollama_keep_alive_is_top_level() -> None:
    from PIL import Image
    from scripts.screen_watcher import ScreenWatcher

    watcher = ScreenWatcher()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "response": '{"app":"Safari","activity":"test","mood":"focused","notable":null}'
    }
    client = AsyncMock()
    client.post.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    context.__aexit__.return_value = None

    with (
        patch.object(ScreenWatcher, "_is_voice_busy", return_value=False),
        patch("scripts.screen_watcher.httpx.AsyncClient", return_value=context),
    ):
        result = await watcher._analyze_with_ollama(
            Image.new("RGB", (64, 64)), "Safari", {},
        )

    assert result is not None
    payload = client.post.await_args.kwargs["json"]
    assert payload["keep_alive"] == "30s"
    assert "keep_alive" not in payload["options"]


@pytest.mark.asyncio
async def test_screen_vision_task_is_cancelled_for_voice_priority() -> None:
    from scripts.screen_watcher import ScreenWatcher

    watcher = ScreenWatcher()

    async def _wait_forever():
        await asyncio.Event().wait()

    watcher._vision_task = asyncio.create_task(_wait_forever())
    await asyncio.sleep(0)
    watcher.defer_for_voice()
    with pytest.raises(asyncio.CancelledError):
        await watcher._vision_task


@pytest.mark.asyncio
async def test_urgent_notification_is_not_blocked_by_cooldown() -> None:
    from scripts.jarvis_daemon import JarvisDaemon

    daemon = JarvisDaemon()
    daemon.running = True
    daemon.mode = "veille"
    daemon.tts_cooldown = 15
    daemon.last_tts_time = time.time()
    daemon.tts_queue.put_nowait(("Fond", "neutral", "background"))
    daemon.tts_queue.put_nowait(("Urgence", "urgent", "critical"))

    with (
        patch("scripts.jarvis_daemon.config.is_quiet_hours", return_value=False),
        patch("database.is_dnd_active", return_value=False),
        patch("scripts.jarvis_daemon.voice_queue.enqueue", new_callable=AsyncMock) as enqueue,
    ):
        task = asyncio.create_task(daemon._tts_loop())
        await asyncio.sleep(0.2)
        daemon.stop()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert any(
        call.args[0] == "Urgence" and call.kwargs["priority"] == VoicePriority.CRITICAL
        for call in enqueue.await_args_list
    )

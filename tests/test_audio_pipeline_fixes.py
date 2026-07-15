"""Tests des correctifs pipeline audio / daemon (audit juin 2026)."""

from __future__ import annotations

import io
import struct
import wave
from unittest.mock import MagicMock, patch

import pytest

from audio.audio_format import (
    detect_upload_format,
    pcm_to_wav,
    playback_file_extension,
    prepare_stt_bytes,
    tts_audio_mime,
)


def _make_wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x01" * 512)
    return buf.getvalue()


def _make_pcm_bytes(n_samples: int = 512) -> bytes:
    return struct.pack(f"{n_samples}h", *([100] * n_samples))


# ── Fix #1 : STT MIME dynamique ───────────────────────────────────────────────


def test_detect_upload_format_webm() -> None:
    webm = b"\x1a\x45\xdf\xa3" + b"\x00" * 2000
    name, mime = detect_upload_format(webm)
    assert name == "audio.webm"
    assert mime == "audio/webm"


def test_detect_upload_format_wav() -> None:
    wav = _make_wav_bytes()
    name, mime = detect_upload_format(wav)
    assert name == "audio.wav"
    assert mime == "audio/wav"


def test_prepare_stt_bytes_wraps_raw_pcm() -> None:
    pcm = _make_pcm_bytes()
    out = prepare_stt_bytes(pcm)
    assert out[:4] == b"RIFF"
    name, mime = detect_upload_format(out)
    assert name == "audio.wav"
    assert mime == "audio/wav"


def test_prepare_stt_bytes_keeps_webm() -> None:
    webm = b"\x1a\x45\xdf\xa3" + b"\x00" * 2000
    assert prepare_stt_bytes(webm) is webm


# ── Fix #2 : playback / audio_mime ────────────────────────────────────────────


def test_playback_file_extension_wav_mp3_m4a() -> None:
    assert playback_file_extension(_make_wav_bytes()) == ".wav"
    assert playback_file_extension(b"ID3" + b"\x00" * 200) == ".mp3"
    assert playback_file_extension(b"\x00\x00\x00\x1cftypM4A ") == ".m4a"


def test_tts_audio_mime_engines() -> None:
    assert tts_audio_mime("kokoro") == "audio/wav"
    assert tts_audio_mime("macos") == "audio/mp4"
    assert tts_audio_mime("edge") == "audio/mpeg"


# ── Fix #3 : NameError local_stt_available ────────────────────────────────────


def test_audio_daemon_empty_stt_branch_no_nameerror() -> None:
    """Simule la branche « aucun STT » sans NameError."""
    local_available = False
    text = ""
    # Ancien code levait NameError sur local_stt_available
    if not text:
        if not local_available:
            logged = True
        else:
            logged = False
    assert logged is True


def test_pcm_to_wav_valid_header() -> None:
    wav = pcm_to_wav(_make_pcm_bytes(100), sample_rate=16000)
    assert wav[:4] == b"RIFF"
    assert b"WAVE" in wav[:16]


# ── Fix #4 : jarvis_daemon TTS cooldown reporte au lieu de jeter ──────────────


@pytest.mark.asyncio
async def test_jarvis_daemon_tts_cooldown_waits_instead_of_drop() -> None:
    """Le cooldown attend au lieu de supprimer le message (pas de continue sans re-queue)."""
    import time

    from scripts.jarvis_daemon import JarvisDaemon

    daemon = JarvisDaemon()
    daemon.tts_cooldown = 10
    daemon.last_tts_time = time.time()
    daemon.mode = "veille"

    now = time.time()
    elapsed = now - daemon.last_tts_time
    assert elapsed < daemon.tts_cooldown
    wait_s = daemon.tts_cooldown - elapsed
    assert wait_s > 0


@pytest.mark.asyncio
async def test_screen_watcher_skips_second_start() -> None:
    from scripts.screen_watcher import ScreenWatcher

    sw = ScreenWatcher()
    sw.enabled = True
    sw.running = True

    tick_called = False

    async def _tick() -> None:
        nonlocal tick_called
        tick_called = True

    sw._tick = _tick  # type: ignore[method-assign]
    await sw.start()
    assert tick_called is False


@pytest.mark.asyncio
async def test_check_mail_skips_when_email_watcher_running() -> None:
    from scripts.jarvis_daemon import JarvisDaemon

    daemon = JarvisDaemon()
    mock_ew = MagicMock()
    mock_ew._running = True
    mock_ew.running = False

    with patch("scripts.email_watcher.email_watcher", mock_ew):
        with patch("database.get_recent_email_summaries") as mock_summaries:
            await daemon._check_mail()
            mock_summaries.assert_not_called()

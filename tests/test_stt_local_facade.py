"""Contrats de la façade STT locale partagée par le web et le daemon."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_raw_pcm_is_forwarded_to_local_backend(monkeypatch):
    import config
    from audio.stt_daemon import DaemonSTT

    instance = DaemonSTT()
    backend = AsyncMock(return_value={"text": "Bonjour", "engine": "faster-whisper"})
    monkeypatch.setattr(instance, "transcribe_with_metadata", backend)

    text = await instance.transcribe(b"\x01\x00" * 1000, language="fr")

    assert text == "Bonjour"
    assert instance.last_raw_text == "Bonjour"
    assert instance.last_clean_text == "Bonjour"
    backend.assert_awaited_once_with(
        b"\x01\x00" * 1000,
        sample_rate=config.AUDIO_DAEMON_SAMPLE_RATE,
        language="fr",
    )


@pytest.mark.asyncio
async def test_browser_container_is_decoded_before_local_transcription(monkeypatch):
    from audio.stt_daemon import DaemonSTT

    instance = DaemonSTT()
    decoded = b"\x02\x00" * 1000
    monkeypatch.setattr(instance, "_decode_media_bytes", lambda _audio: decoded)
    backend = AsyncMock(return_value={"text": "Test web", "engine": "faster-whisper"})
    monkeypatch.setattr(instance, "transcribe_with_metadata", backend)

    media = b"\x1a\x45\xdf\xa3" + b"x" * 1996
    text = await instance.transcribe(media, language="fr", timeout=5.0)

    assert text == "Test web"
    backend.assert_awaited_once_with(decoded, sample_rate=16000, language="fr")


@pytest.mark.asyncio
async def test_too_short_audio_is_ignored(monkeypatch):
    from audio.stt_daemon import DaemonSTT

    instance = DaemonSTT()
    backend = AsyncMock()
    monkeypatch.setattr(instance, "transcribe_with_metadata", backend)

    assert await instance.transcribe(b"x" * 10) == ""
    backend.assert_not_awaited()


@pytest.mark.asyncio
async def test_diarization_is_disabled_until_a_local_engine_exists():
    from audio.stt_daemon import DaemonSTT

    instance = DaemonSTT()
    assert await instance.transcribe_with_diarization(b"x" * 2000) == []


def test_audio_package_exports_local_singleton():
    from audio import stt
    from audio.stt_daemon import stt_daemon

    assert stt is stt_daemon

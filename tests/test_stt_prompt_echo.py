"""Anti-régression STT : décodage M4A Android + filtre d'écho du prompt."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from audio.audio_format import is_encoded_audio_container, is_mpeg4_container
from audio.stt_daemon import (
    DEFAULT_INITIAL_PROMPT,
    build_initial_prompt,
    is_stt_prompt_echo,
)


def test_default_initial_prompt_is_not_app_list():
    prompt = DEFAULT_INITIAL_PROMPT.lower()
    assert "blue snowball" not in prompt
    assert "visual studio" not in prompt
    assert "messages" not in prompt
    assert "jarvis" in prompt
    assert "français" in prompt or "francais" in prompt


def test_build_initial_prompt_limits_keyterms(monkeypatch):
    monkeypatch.setattr(
        "config.VOICE_STT_KEYTERMS",
        "JARVIS,DeepSeek,Messages,Mail,Calendar,Safari,Terminal,VSCode,Extra,TooMany",
        raising=False,
    )
    prompt = build_initial_prompt()
    # Max 8 keyterms — pas de fuite de longueur style ancien prompt
    assert prompt.count(",") <= 10
    assert "Blue Snowball" not in prompt


def test_is_stt_prompt_echo_detects_legacy_app_list():
    ghost = "JARVIS, DeepSeek, Messages, Mail, Calendar, Visual Studio Code, Blue Snowball"
    assert is_stt_prompt_echo(ghost) is True


def test_is_stt_prompt_echo_allows_real_french():
    assert is_stt_prompt_echo("Quel temps fait-il à Lille aujourd'hui ?") is False
    assert is_stt_prompt_echo("Ouvre Messages et Mail") is False
    assert is_stt_prompt_echo("bonjour") is False


def test_mpeg4_container_detected():
    m4a = b"\x00\x00\x00\x1cftypM4A " + b"\x00" * 64
    assert is_mpeg4_container(m4a) is True
    assert is_encoded_audio_container(m4a) is True
    assert is_encoded_audio_container(b"\x01\x00" * 100) is False


@pytest.mark.asyncio
async def test_m4a_container_is_decoded_before_transcription(monkeypatch):
    from audio.stt_daemon import DaemonSTT

    instance = DaemonSTT()
    decoded = b"\x02\x00" * 1000
    monkeypatch.setattr(instance, "_decode_media_bytes", lambda _audio: decoded)
    backend = AsyncMock(return_value={"text": "Bonjour", "engine": "faster-whisper"})
    monkeypatch.setattr(instance, "transcribe_with_metadata", backend)

    m4a = b"\x00\x00\x00\x1cftypM4A " + b"x" * 2000
    text = await instance.transcribe(m4a, language="fr", timeout=5.0)

    assert text == "Bonjour"
    backend.assert_awaited_once_with(decoded, sample_rate=16000, language="fr")

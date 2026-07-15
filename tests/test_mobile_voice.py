"""Contrat POST /api/mobile/voice/turn — push-to-talk Android."""

from __future__ import annotations

import io
import struct
import sys
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.conftest import authenticate  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "mobile_voice.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def _pair(client, device_id: str = "pixel-voice") -> str:
    start = client.post("/api/mobile/pairing/start")
    code = start.json()["code"]
    complete = client.post(
        "/api/mobile/pairing/complete",
        json={
            "code": code,
            "device_id": device_id,
            "name": "Pixel Test",
            "model": "Pixel 8",
            "app_version": "1.0.4",
        },
    )
    assert complete.status_code == 200
    return complete.json()["token"]


def _make_wav(duration_ms: int = 500, sample_rate: int = 16000) -> bytes:
    frames = int(sample_rate * duration_ms / 1000)
    pcm = struct.pack(f"<{frames}h", *([100] * frames))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _post_turn(client, token: str, wav: bytes, conversation_id: int | None = None):
    data = {}
    if conversation_id is not None:
        data["conversation_id"] = str(conversation_id)
    return client.post(
        "/api/mobile/voice/turn",
        headers={"Authorization": f"Bearer {token}"},
        files={"audio": ("turn.wav", wav, "audio/wav")},
        data=data,
    )


def test_voice_turn_requires_token(tmp_db):
    wav = _make_wav()
    with _client() as client:
        resp = _post_turn(client, "", wav)
    assert resp.status_code == 401


def test_voice_turn_rejects_revoked_token(tmp_db):
    import auth
    from database import revoke_mobile_device

    with _client() as client:
        authenticate(client)
        token = _pair(client, "revoked-voice")
        revoke_mobile_device("revoked-voice")
        resp = _post_turn(client, token, _make_wav())
    assert resp.status_code == 401
    assert auth.verify_mobile_token(token) is None


def test_voice_turn_rejects_empty_audio(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        resp = _post_turn(client, token, b"")
    assert resp.status_code == 400


def test_voice_turn_rejects_invalid_format(tmp_db):
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        resp = _post_turn(client, token, b"not-audio" * 200)
    assert resp.status_code == 415


def test_voice_turn_rejects_oversized_payload(tmp_db, monkeypatch):
    import config

    monkeypatch.setattr(config, "MOBILE_VOICE_MAX_BYTES", 500)
    wav = _make_wav(duration_ms=2000)
    with _client() as client:
        authenticate(client)
        token = _pair(client)
        resp = _post_turn(client, token, wav)
    assert resp.status_code == 413


@patch("api.mobile_voice_service.stt_local")
@patch("api.mobile_voice_service._process_message_internal", new_callable=AsyncMock)
@patch("api.mobile_voice_service.get_tts_by_name")
def test_voice_turn_rejects_stt_prompt_echo(mock_get_tts, mock_llm, mock_stt, tmp_db):
    """Whisper qui recopie le prompt (liste d'apps) ne doit pas atteindre le LLM."""
    mock_stt.available = True
    mock_stt.transcribe = AsyncMock(
        return_value="JARVIS, DeepSeek, Messages, Mail, Calendar, Visual Studio Code, Blue Snowball"
    )
    mock_get_tts.return_value = AsyncMock()

    with _client() as client:
        authenticate(client)
        token = _pair(client, "echo-voice")
        resp = _post_turn(client, token, _make_wav())

    assert resp.status_code == 400
    assert "entendu" in resp.json()["detail"].lower() or "bien" in resp.json()["detail"].lower()
    mock_llm.assert_not_awaited()


@patch("api.mobile_voice_service.resolve_tts_engine_name", return_value="edge")
@patch("api.mobile_voice_service.stt_local")
@patch("api.mobile_voice_service._process_message_internal", new_callable=AsyncMock)
@patch("api.mobile_voice_service.get_tts_by_name")
def test_voice_turn_happy_path(mock_get_tts, mock_llm, mock_stt, mock_resolve_tts, tmp_db):
    mock_stt.available = True
    mock_stt.preload_sync.return_value = True
    mock_stt.transcribe = AsyncMock(return_value="quel temps fait-il")
    mock_llm.return_value = {
        "text": "Lille, dix-huit degrés, couvert.",
        "emotion": "neutral",
        "agent": "info",
        "model": "deepseek-v4-flash",
        "cost": 0.001,
    }
    tts = AsyncMock()
    tts.available = True
    tts.synthesize = AsyncMock(return_value=b"\xff\xf3fake")
    tts.get_backend_name = lambda: "edge"
    mock_get_tts.return_value = tts

    wav = _make_wav()
    with _client() as client:
        authenticate(client)
        token = _pair(client, "happy-voice")
        resp = _post_turn(client, token, wav)

    assert resp.status_code == 200
    body = resp.json()
    assert body["transcript"] == "quel temps fait-il"
    assert "Lille" in body["response_text"]
    assert body["source"] == "android_voice"
    assert body["device_id"] == "happy-voice"
    assert body["stt_engine"] == "faster-whisper"
    assert body["tts_engine"] == "edge"
    assert body.get("tts_voice") == "fr-FR-HenriNeural" or "Henri" in (body.get("tts_voice") or "")
    assert body["conversation_id"] > 0
    mock_stt.transcribe.assert_awaited_once()
    mock_llm.assert_awaited_once_with("quel temps fait-il", body["conversation_id"], voice_mode=True)
    tts.synthesize.assert_awaited_once()


@patch("api.mobile_voice_service.stt_local")
@patch("api.mobile_voice_service._process_message_internal", new_callable=AsyncMock)
@patch("api.mobile_voice_service.get_tts_by_name")
def test_voice_turn_text_without_tts_when_synthesis_fails(
    mock_get_tts, mock_llm, mock_stt, tmp_db
):
    mock_stt.available = True
    mock_stt.transcribe = AsyncMock(return_value="bonjour")
    mock_llm.return_value = {"text": "Bonjour Monsieur.", "emotion": "warm", "agent": "info"}
    tts = AsyncMock()
    tts.synthesize = AsyncMock(side_effect=RuntimeError("kokoro down"))
    tts.get_backend_name = lambda: "kokoro"
    mock_get_tts.return_value = tts

    with _client() as client:
        authenticate(client)
        token = _pair(client, "tts-fail")
        resp = _post_turn(client, token, _make_wav())

    assert resp.status_code == 200
    body = resp.json()
    assert body["response_text"] == "Bonjour Monsieur."
    assert body.get("audio_base64") in (None, "")
    assert body.get("tts_error")


@patch("api.mobile_voice_service.stt_local")
@patch("api.mobile_voice_service._process_message_internal", new_callable=AsyncMock)
@patch("api.mobile_voice_service.get_tts_by_name")
def test_voice_turn_preserves_conversation_context(
    mock_get_tts, mock_llm, mock_stt, tmp_db
):
    mock_stt.available = True
    mock_stt.transcribe = AsyncMock(side_effect=["première question", "deuxième question"])
    mock_llm.return_value = {"text": "Réponse.", "emotion": "neutral", "agent": "info"}
    tts = AsyncMock()
    tts.synthesize = AsyncMock(return_value=b"RIFF")
    tts.get_backend_name = lambda: "kokoro"
    mock_get_tts.return_value = tts

    wav = _make_wav()
    with _client() as client:
        authenticate(client)
        token = _pair(client, "ctx-voice")
        first = _post_turn(client, token, wav)
        conv_id = first.json()["conversation_id"]
        second = _post_turn(client, token, wav, conversation_id=conv_id)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["conversation_id"] == conv_id
    assert mock_llm.await_count == 2
    second_call = mock_llm.await_args_list[1]
    assert second_call.args[1] == conv_id


def test_voice_fixture_wav_integration_smoke(tmp_db, monkeypatch):
    """Intégration légère : WAV valide, STT/LLM/TTS mockés."""
    fixture = PROJECT_ROOT / "tests" / "fixtures" / "mobile_voice_silence.wav"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    if not fixture.exists():
        fixture.write_bytes(_make_wav())

    with patch("api.mobile_voice_service.stt_local") as mock_stt, patch(
        "api.mobile_voice_service._process_message_internal", new_callable=AsyncMock
    ) as mock_llm, patch("api.mobile_voice_service.get_tts_by_name") as mock_get_tts:
        mock_stt.available = True
        mock_stt.transcribe = AsyncMock(return_value="test")
        mock_llm.return_value = {"text": "OK", "emotion": "neutral", "agent": "info"}
        tts = AsyncMock()
        tts.synthesize = AsyncMock(return_value=fixture.read_bytes())
        tts.get_backend_name = lambda: "kokoro"
        mock_get_tts.return_value = tts

        with _client() as client:
            authenticate(client)
            token = _pair(client, "fixture")
            resp = _post_turn(client, token, fixture.read_bytes())

    assert resp.status_code == 200
    assert resp.json()["audio_base64"]

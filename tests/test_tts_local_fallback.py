"""Repli TTS local quand Edge est injoignable — aucun accès réseau requis.

Trois garanties vérifiées ici :

1. la chaîne native (`audio.tts_native.get_native_tts_engine`) ne retourne
   jamais le moteur réseau, quelle que soit la configuration ;
2. Kokoro qui échoue replie sur macOS `say` en WAV, jamais sur Edge ;
3. le chemin WebSocket survit à un Edge injoignable : il annonce la fin de
   parole au lieu de laisser le client attendre.

Un scénario est marqué `integration_tts` : il fait réellement parler macOS.
Il reste dans la suite standard (hors ligne, déterministe) et se saute
proprement hors macOS.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from typing import Any

import pytest

import config
from tests.edge_tts_double import FakeEdgeTTS
from tests.tts_contract import CONTAINER_WAV, detect_container

tts_module = importlib.import_module("audio.tts")
tts_native = importlib.import_module("audio.tts_native")

TTS_LOGGER = "audio.tts"
LOCAL_WAV = b"RIFF" + bytes(4_000)
MIN_LOCAL_AUDIO_BYTES = 2_000
LOCAL_ENGINE_NAMES = frozenset({"kokoro", "macos", "ttskit"})


@pytest.fixture(autouse=True)
def emitted_events(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Capture les événements du bus : pas de tâche de fond pendant les tests."""
    events: list[Any] = []
    monkeypatch.setattr(tts_module, "_emit_background", events.append)
    return events


class FakeWebSocket:
    """Collecte ce que le serveur envoie au client (JSON et binaire)."""

    def __init__(self) -> None:
        self.json_messages: list[dict[str, Any]] = []
        self.binary_frames: list[bytes] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.json_messages.append(data)

    async def send_bytes(self, data: bytes) -> None:
        self.binary_frames.append(data)

    @property
    def message_types(self) -> list[str]:
        return [message.get("type", "") for message in self.json_messages]


# ── 1. La chaîne locale ignore toujours le moteur réseau ─────────────────────


@pytest.mark.parametrize("configured", ["edge", "kokoro", "macos", "ttskit", ""])
def test_native_chain_never_returns_the_network_engine(monkeypatch, configured: str):
    monkeypatch.setattr(config, "TTS_ENGINE", configured)
    monkeypatch.setattr(tts_module.kokoro_tts, "available", True)
    monkeypatch.setattr(tts_module.macos_tts, "available", True)
    monkeypatch.setattr(tts_native.ttskit_tts, "preload_sync", lambda: False)

    engine = tts_native.get_native_tts_engine()

    assert engine is not tts_module.tts
    assert engine.get_backend_name() in LOCAL_ENGINE_NAMES


def test_native_chain_degrades_to_macos_when_kokoro_is_absent(monkeypatch):
    monkeypatch.setattr(config, "TTS_ENGINE", "kokoro")
    monkeypatch.setattr(tts_module.kokoro_tts, "available", False)
    monkeypatch.setattr(tts_module.macos_tts, "available", True)
    monkeypatch.setattr(tts_native.ttskit_tts, "preload_sync", lambda: False)

    assert tts_native.get_native_tts_engine() is tts_module.macos_tts


def test_native_chain_returns_nothing_rather_than_falling_back_to_edge(monkeypatch):
    monkeypatch.setattr(config, "TTS_ENGINE", "kokoro")
    monkeypatch.setattr(tts_module.kokoro_tts, "available", False)
    monkeypatch.setattr(tts_module.macos_tts, "available", False)
    monkeypatch.setattr(tts_native.ttskit_tts, "preload_sync", lambda: False)

    assert tts_native.get_native_tts_engine() is None


@pytest.mark.parametrize("name", ["kokoro", "macos"])
def test_get_tts_by_name_returns_edge_only_when_edge_is_asked(monkeypatch, name: str):
    monkeypatch.setattr(tts_module.kokoro_tts, "available", False)
    monkeypatch.setattr(tts_module.macos_tts, "available", False)

    assert tts_module.get_tts_by_name(name) is not tts_module.tts
    assert tts_module.get_tts_by_name("edge") is tts_module.tts


# ── 2. Kokoro en échec → macOS local, jamais Edge ────────────────────────────


def _prepare_kokoro(monkeypatch: pytest.MonkeyPatch) -> Any:
    kokoro = tts_module.kokoro_tts
    monkeypatch.setattr(kokoro, "available", True)
    monkeypatch.setattr(kokoro, "_backend", "mlx")
    monkeypatch.setattr(kokoro, "_ensure_loaded", lambda: True)
    return kokoro


def _prepare_macos(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def _synthesize_native(text: str, emotion: str = "neutral") -> bytes:
        calls.append((text, emotion))
        return LOCAL_WAV

    monkeypatch.setattr(tts_module.macos_tts, "available", True)
    monkeypatch.setattr(tts_module.macos_tts, "synthesize_native", _synthesize_native)
    return calls


async def test_kokoro_failure_falls_back_to_macos_wav(monkeypatch):
    kokoro = _prepare_kokoro(monkeypatch)
    calls = _prepare_macos(monkeypatch)

    async def _boom(_text: str) -> bytes:
        raise RuntimeError("sidecar mlx-audio absent")

    monkeypatch.setattr(kokoro, "_synthesize_mlx", _boom)

    audio = await kokoro.synthesize("Bonjour Monsieur.", emotion="warm")

    assert audio == LOCAL_WAV
    assert calls == [("Bonjour Monsieur.", "warm")]


async def test_kokoro_empty_output_falls_back_to_macos(monkeypatch, caplog):
    kokoro = _prepare_kokoro(monkeypatch)
    calls = _prepare_macos(monkeypatch)

    async def _empty(_text: str) -> bytes:
        return b""

    monkeypatch.setattr(kokoro, "_synthesize_mlx", _empty)

    with caplog.at_level(logging.WARNING, logger=TTS_LOGGER):
        audio = await kokoro.synthesize("Bonjour Monsieur.")

    assert audio == LOCAL_WAV
    assert len(calls) == 1
    assert "fallback macOS" in caplog.text


async def test_kokoro_unloadable_model_falls_back_without_touching_edge(monkeypatch):
    kokoro = tts_module.kokoro_tts
    monkeypatch.setattr(kokoro, "_ensure_loaded", lambda: False)
    calls = _prepare_macos(monkeypatch)
    fake_edge = FakeEdgeTTS()
    monkeypatch.setitem(sys.modules, "edge_tts", fake_edge)

    audio = await kokoro.synthesize("Bonjour Monsieur.")

    assert audio == LOCAL_WAV
    assert len(calls) == 1
    assert fake_edge.calls == [], "le repli Kokoro ne doit jamais appeler Edge"


def test_kokoro_fallback_target_is_the_local_engine(monkeypatch):
    monkeypatch.setattr(tts_module.macos_tts, "available", False)

    assert tts_module.kokoro_tts.get_fallback() is tts_module.macos_tts


# ── 3. Chemin WebSocket : Edge injoignable ne bloque pas le client ───────────


async def test_websocket_path_closes_speech_when_edge_is_unreachable(monkeypatch, caplog):
    chat_context = importlib.import_module("api.chat_context")
    tts_cache = importlib.import_module("audio.tts_cache")

    fake_edge = FakeEdgeTTS(error=ConnectionRefusedError(61, "Connection refused"))
    monkeypatch.setitem(sys.modules, "edge_tts", fake_edge)
    edge_engine = tts_module.TTSEngine()
    monkeypatch.setattr(tts_module, "get_tts_by_name", lambda _name: edge_engine)
    monkeypatch.setattr(tts_module, "resolve_tts_engine_name", lambda: "edge")
    monkeypatch.setattr(tts_cache.speculative_tts, "get", lambda *a, **k: None)
    monkeypatch.setattr(tts_cache.last_tts, "store", lambda *a, **k: None)

    ws = FakeWebSocket()
    with caplog.at_level(logging.WARNING, logger=TTS_LOGGER):
        status = await chat_context._send_tts_streaming(
            ws,  # type: ignore[arg-type]
            "Bonjour Monsieur.",
            "neutral",
            turn_id="turn-offline",
        )

    assert status == "completed"
    assert ws.message_types == ["speaking", "speech_done"]
    assert ws.binary_frames == []
    assert ws.json_messages[0]["audio_mime"] == "audio/mpeg"
    assert "service injoignable" in caplog.text


async def test_websocket_path_skips_speech_when_engine_is_unavailable(monkeypatch):
    chat_context = importlib.import_module("api.chat_context")

    class UnavailableEngine:
        available = False

        def get_backend_name(self) -> str:
            return "none"

    monkeypatch.setattr(tts_module, "get_tts_by_name", lambda _name: UnavailableEngine())
    monkeypatch.setattr(tts_module, "resolve_tts_engine_name", lambda: "edge")

    ws = FakeWebSocket()
    status = await chat_context._send_tts_streaming(
        ws,  # type: ignore[arg-type]
        "Bonjour Monsieur.",
        "neutral",
    )

    assert status == "skipped"
    assert ws.message_types == ["speaking", "speech_done"]
    assert ws.binary_frames == []


# ── 4. Intégration locale réelle (macOS say) ────────────────────────────────


@pytest.mark.integration_tts
@pytest.mark.skipif(sys.platform != "darwin", reason="`say`/`afconvert` sont propres à macOS")
async def test_macos_engine_really_produces_wav_offline():
    """Preuve que le repli hors ligne parle vraiment, sans mock ni réseau."""
    engine = tts_module.macos_tts
    if not engine.available:
        pytest.skip("commandes `say`/`afconvert` indisponibles sur cette machine")

    audio = await asyncio.wait_for(
        engine.synthesize_native("Bonjour Monsieur. Repli local."), timeout=30
    )

    assert detect_container(audio) == CONTAINER_WAV
    assert len(audio) > MIN_LOCAL_AUDIO_BYTES, f"audio trop court : {len(audio)} octets"

"""Tests unitaires du TTS Edge — `edge_tts` simulé, aucun octet vers Internet.

Le scénario réseau réel vit dans `tests/test_tts_edge_external.py` (marqueur
`external_network`, exclu de la suite standard). Ici, tout est déterministe et
hors ligne : paramètres transmis à Edge, parsing du flux, erreurs, délais,
classification des échecs et contrat du payload audio.
"""

from __future__ import annotations

import asyncio
import errno
import importlib
import logging
import socket
import ssl
import sys
import time
from typing import Any

import pytest

import config
from audio.tts_errors import (
    TTSFailureKind,
    classify_tts_failure,
    describe_tts_failure,
    is_network_unavailable,
)
from tests.edge_tts_double import (
    EXPECTED_AUDIO,
    FRAME_CHUNK,
    FRENCH_VOICE,
    ID3_CHUNK,
    FakeEdgeTTS,
)
from tests.tts_contract import (
    CONTAINER_EMPTY,
    CONTAINER_M4A,
    CONTAINER_MP3,
    CONTAINER_OGG,
    CONTAINER_UNKNOWN,
    CONTAINER_WAV,
    MIN_MP3_BYTES,
    assert_mp3_payload,
    describe_payload,
    detect_container,
    mp3_payload_violations,
)

# `audio/__init__.py` réexporte le singleton `tts`, qui masque le sous-module :
# seul `import_module` rend le module `audio.tts` lui-même.
tts_module = importlib.import_module("audio.tts")

TTS_LOGGER = "audio.tts"
SAMPLE_TEXT = "Bonjour Monsieur. Test de la voix française."

WAV_PAYLOAD = b"RIFF" + bytes(2000)
M4A_PAYLOAD = b"\x00\x00\x00\x20ftypM4A " + bytes(2000)
OGG_PAYLOAD = b"OggS" + bytes(2000)


@pytest.fixture(autouse=True)
def _french_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Réglages TTS explicites : les tests ne dépendent pas du `.env` local."""
    monkeypatch.setattr(config, "TTS_VOICE", FRENCH_VOICE)
    monkeypatch.setattr(config, "EDGE_TTS_CONNECT_TIMEOUT_SEC", 10)
    monkeypatch.setattr(config, "EDGE_TTS_RECEIVE_TIMEOUT_SEC", 60)
    monkeypatch.setattr(config, "EDGE_TTS_TOTAL_TIMEOUT_SEC", 30.0)


@pytest.fixture(autouse=True)
def emitted_events(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Capture les événements du bus : pas de tâche de fond pendant les tests."""
    events: list[Any] = []
    monkeypatch.setattr(tts_module, "_emit_background", events.append)
    return events


def _engine(monkeypatch: pytest.MonkeyPatch, fake: FakeEdgeTTS) -> tts_module.TTSEngine:
    """Instancie un moteur Edge dont le module `edge_tts` est simulé."""
    monkeypatch.setitem(sys.modules, "edge_tts", fake)
    engine = tts_module.TTSEngine()
    assert engine.available is True
    assert engine.get_backend_name() == "edge"
    return engine


async def _collect(engine: tts_module.TTSEngine, text: str = SAMPLE_TEXT) -> list[bytes]:
    return [chunk async for chunk in engine.synthesize_stream(text)]


# ── 1. Paramètres transmis à edge-tts ────────────────────────────────────────


async def test_synthesize_sends_text_and_configured_french_voice(monkeypatch):
    fake = FakeEdgeTTS()
    engine = _engine(monkeypatch, fake)

    await engine.synthesize(SAMPLE_TEXT)

    assert fake.last_call.text == SAMPLE_TEXT
    assert fake.last_call.voice == FRENCH_VOICE


async def test_synthesize_uses_henri_when_voice_setting_is_empty(monkeypatch):
    """Un `TTS_VOICE` vide ferait parler Edge en anglais (voix par défaut)."""
    monkeypatch.setattr(config, "TTS_VOICE", "")
    fake = FakeEdgeTTS()
    engine = _engine(monkeypatch, fake)

    await engine.synthesize(SAMPLE_TEXT)

    assert fake.last_call.voice == "fr-FR-HenriNeural"


async def test_synthesize_applies_configured_network_timeouts(monkeypatch):
    monkeypatch.setattr(config, "EDGE_TTS_CONNECT_TIMEOUT_SEC", 3)
    monkeypatch.setattr(config, "EDGE_TTS_RECEIVE_TIMEOUT_SEC", 7)
    fake = FakeEdgeTTS()
    engine = _engine(monkeypatch, fake)

    await engine.synthesize(SAMPLE_TEXT)

    assert fake.last_call.kwargs == {"connect_timeout": 3, "receive_timeout": 7}


async def test_stream_and_buffered_paths_share_the_same_edge_parameters(monkeypatch):
    fake = FakeEdgeTTS()
    engine = _engine(monkeypatch, fake)

    await engine.synthesize(SAMPLE_TEXT)
    await _collect(engine)

    assert len(fake.calls) == 2
    assert fake.calls[0] == fake.calls[1]


# ── 2. Parsing du flux Edge ──────────────────────────────────────────────────


async def test_synthesize_concatenates_audio_messages_only(monkeypatch):
    engine = _engine(monkeypatch, FakeEdgeTTS())

    audio = await engine.synthesize(SAMPLE_TEXT)

    assert audio == EXPECTED_AUDIO


async def test_synthesize_result_satisfies_the_mp3_contract(monkeypatch):
    engine = _engine(monkeypatch, FakeEdgeTTS())

    audio = await engine.synthesize(SAMPLE_TEXT)

    assert_mp3_payload(audio)


async def test_stream_yields_audio_chunks_in_order(monkeypatch):
    engine = _engine(monkeypatch, FakeEdgeTTS())

    chunks = await _collect(engine)

    assert chunks == [ID3_CHUNK, FRAME_CHUNK]


async def test_stream_ignores_prosody_markers_and_empty_chunks(monkeypatch):
    messages = [
        {"type": "WordBoundary", "offset": 0, "duration": 10, "text": "Bonjour"},
        {"type": "SentenceBoundary", "offset": 10, "duration": 10},
        {"type": "audio", "data": b""},
        {"type": "audio", "data": None},
        {"type": "audio"},
    ]
    engine = _engine(monkeypatch, FakeEdgeTTS(messages=messages))

    assert await _collect(engine) == []


# ── 3. Refus locaux : aucun appel réseau déclenché ───────────────────────────


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
async def test_blank_text_is_refused_without_calling_edge(monkeypatch, text: str):
    fake = FakeEdgeTTS()
    engine = _engine(monkeypatch, fake)

    assert await engine.synthesize(text) == b""
    assert await _collect(engine, text) == []
    assert fake.calls == []


async def test_unavailable_engine_never_calls_edge(monkeypatch):
    fake = FakeEdgeTTS()
    engine = _engine(monkeypatch, fake)
    monkeypatch.setattr(engine, "available", False)

    assert await engine.synthesize(SAMPLE_TEXT) == b""
    assert await _collect(engine) == []
    assert fake.calls == []


def test_engine_is_unavailable_when_edge_tts_is_not_installed(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "edge_tts", None)

    with caplog.at_level(logging.WARNING, logger=TTS_LOGGER):
        engine = tts_module.TTSEngine()

    assert engine.available is False
    assert engine.get_backend_name() == "none"
    assert "edge-tts non installé" in caplog.text


async def test_missing_edge_tts_returns_empty_audio_without_raising(monkeypatch):
    engine = _engine(monkeypatch, FakeEdgeTTS())
    monkeypatch.setitem(sys.modules, "edge_tts", None)

    assert await engine.synthesize(SAMPLE_TEXT) == b""
    assert await _collect(engine) == []


async def test_response_is_never_written_to_a_temporary_file(monkeypatch):
    """Le texte lu par JARVIS est privé : rien ne doit atterrir dans /tmp."""

    def _forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("La synthèse Edge ne doit créer aucun fichier temporaire")

    engine = _engine(monkeypatch, FakeEdgeTTS())
    monkeypatch.setattr(tts_module.tempfile, "mkstemp", _forbidden)
    monkeypatch.setattr(tts_module.tempfile, "NamedTemporaryFile", _forbidden)

    assert await engine.synthesize(SAMPLE_TEXT) == EXPECTED_AUDIO


@pytest.mark.parametrize("emotion", ["inconnue", "", "URGENT"])
async def test_unknown_emotion_is_normalized_without_breaking_synthesis(
    monkeypatch, emotion: str
):
    engine = _engine(monkeypatch, FakeEdgeTTS())

    assert await engine.synthesize(SAMPLE_TEXT, emotion=emotion) == EXPECTED_AUDIO


# ── 4. Erreurs : réseau absent vs contrat rompu ──────────────────────────────


async def test_unreachable_service_returns_empty_and_only_warns(monkeypatch, caplog):
    fake = FakeEdgeTTS(error=ConnectionRefusedError(61, "Connection refused"))
    engine = _engine(monkeypatch, fake)

    with caplog.at_level(logging.DEBUG, logger=TTS_LOGGER):
        audio = await engine.synthesize(SAMPLE_TEXT)

    assert audio == b""
    assert "service injoignable" in caplog.text
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


async def test_functional_failure_returns_empty_and_logs_an_error(monkeypatch, caplog):
    edge_exceptions = pytest.importorskip("edge_tts.exceptions")
    fake = FakeEdgeTTS(error=edge_exceptions.NoAudioReceived("No audio was received"))
    engine = _engine(monkeypatch, fake)

    with caplog.at_level(logging.DEBUG, logger=TTS_LOGGER):
        audio = await engine.synthesize(SAMPLE_TEXT)

    assert audio == b""
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "défaut fonctionnel" in errors[0].getMessage()


async def test_unknown_exception_is_treated_as_functional(monkeypatch, caplog):
    fake = FakeEdgeTTS(error=RuntimeError("réponse inattendue du service"))
    engine = _engine(monkeypatch, fake)

    with caplog.at_level(logging.DEBUG, logger=TTS_LOGGER):
        audio = await engine.synthesize(SAMPLE_TEXT)

    assert audio == b""
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_stream_without_audio_message_logs_an_error(monkeypatch, caplog):
    """Edge qui ne renvoie aucun audio est une régression, pas un incident réseau."""
    messages = [{"type": "WordBoundary", "offset": 0, "duration": 10, "text": "x"}]
    engine = _engine(monkeypatch, FakeEdgeTTS(messages=messages))

    with caplog.at_level(logging.DEBUG, logger=TTS_LOGGER):
        audio = await engine.synthesize(SAMPLE_TEXT)

    assert audio == b""
    assert "aucun audio" in caplog.text


async def test_stream_keeps_chunks_received_before_the_failure(monkeypatch, caplog):
    fake = FakeEdgeTTS(error=ConnectionResetError("Connection reset by peer"), error_after=1)
    engine = _engine(monkeypatch, fake)

    with caplog.at_level(logging.DEBUG, logger=TTS_LOGGER):
        chunks = await _collect(engine)

    assert chunks == [ID3_CHUNK]
    assert "service injoignable" in caplog.text


async def test_buffered_synthesis_discards_partial_audio_on_failure(monkeypatch):
    """Un MP3 tronqué est injouable : mieux vaut rien que du bruit."""
    fake = FakeEdgeTTS(error=ConnectionResetError("reset"), error_after=1)
    engine = _engine(monkeypatch, fake)

    assert await engine.synthesize(SAMPLE_TEXT) == b""


# ── 5. Délais ────────────────────────────────────────────────────────────────


async def test_synthesis_gives_up_after_the_total_timeout(monkeypatch, caplog):
    monkeypatch.setattr(config, "EDGE_TTS_TOTAL_TIMEOUT_SEC", 0.02)
    fake = FakeEdgeTTS(chunk_delay=0.5)
    engine = _engine(monkeypatch, fake)

    started = time.perf_counter()
    with caplog.at_level(logging.DEBUG, logger=TTS_LOGGER):
        audio = await engine.synthesize(SAMPLE_TEXT)
    elapsed = time.perf_counter() - started

    assert audio == b""
    assert elapsed < 0.5, "le plafond global n'a pas interrompu le flux Edge"
    assert fake.stream_count == 1
    assert "service injoignable" in caplog.text


async def test_caller_can_bound_synthesis_with_its_own_timeout(monkeypatch):
    """Contrat utilisé par `api/mobile_voice_service.py` (`MOBILE_VOICE_TTS_TIMEOUT_SEC`)."""
    monkeypatch.setattr(config, "EDGE_TTS_TOTAL_TIMEOUT_SEC", 30.0)
    engine = _engine(monkeypatch, FakeEdgeTTS(chunk_delay=0.5))

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(engine.synthesize(SAMPLE_TEXT), timeout=0.02)


# ── 6. Catalogue de voix ─────────────────────────────────────────────────────


async def test_get_voices_filters_locale_and_maps_fields(monkeypatch):
    engine = _engine(monkeypatch, FakeEdgeTTS())

    voices = await engine.get_voices("fr-FR")

    assert voices == [
        {"name": "fr-FR-HenriNeural", "gender": "Male", "locale": "fr-FR"},
        {"name": "fr-FR-DeniseNeural", "gender": "Female", "locale": "fr-FR"},
    ]


async def test_get_voices_without_filter_returns_every_voice(monkeypatch):
    engine = _engine(monkeypatch, FakeEdgeTTS())

    assert len(await engine.get_voices("")) == 3


async def test_get_voices_returns_empty_when_service_unreachable(monkeypatch, caplog):
    fake = FakeEdgeTTS(voices_error=socket.gaierror(8, "nodename nor servname provided"))
    engine = _engine(monkeypatch, fake)

    with caplog.at_level(logging.DEBUG, logger=TTS_LOGGER):
        assert await engine.get_voices() == []

    assert "service injoignable" in caplog.text


async def test_get_voices_returns_empty_for_a_non_edge_backend(monkeypatch):
    fake = FakeEdgeTTS()
    engine = _engine(monkeypatch, fake)
    monkeypatch.setattr(engine, "_backend", "macos")

    assert await engine.get_voices() == []
    assert fake.list_voices_count == 0


async def test_get_voices_returns_empty_when_edge_tts_is_missing(monkeypatch):
    engine = _engine(monkeypatch, FakeEdgeTTS())
    monkeypatch.setitem(sys.modules, "edge_tts", None)

    assert await engine.get_voices() == []


# ── 7. Événements du bus ─────────────────────────────────────────────────────


async def test_synthesis_emits_start_then_done(monkeypatch, emitted_events):
    engine = _engine(monkeypatch, FakeEdgeTTS())

    await engine.synthesize(SAMPLE_TEXT)

    assert [event.type for event in emitted_events] == ["tts.start", "tts.done"]
    assert emitted_events[0].data == {"engine": "edge", "text_length": len(SAMPLE_TEXT)}


async def test_blank_text_emits_no_event(monkeypatch, emitted_events):
    engine = _engine(monkeypatch, FakeEdgeTTS())

    await engine.synthesize("   ")

    assert emitted_events == []


# ── 8. Classification des échecs (audio/tts_errors.py) ───────────────────────


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionRefusedError(61, "Connection refused"),
        ConnectionResetError("Connection reset by peer"),
        TimeoutError("timed out"),
        asyncio.TimeoutError(),
        socket.gaierror(8, "nodename nor servname provided"),
        ssl.SSLCertVerificationError("certificate verify failed"),
        OSError(errno.ENETUNREACH, "Network is unreachable"),
    ],
    ids=[
        "refus_tcp",
        "reset",
        "timeout",
        "timeout_asyncio",
        "dns",
        "interception_tls",
        "reseau_injoignable",
    ],
)
def test_transport_failures_are_network_unavailable(exc: BaseException):
    assert classify_tts_failure(exc) is TTSFailureKind.NETWORK_UNAVAILABLE
    assert is_network_unavailable(exc) is True


@pytest.mark.parametrize(
    "name",
    ["NoAudioReceived", "UnexpectedResponse", "UnknownResponse", "SkewAdjustmentError"],
)
def test_edge_tts_contract_failures_are_functional(name: str):
    edge_exceptions = pytest.importorskip("edge_tts.exceptions")
    exc = getattr(edge_exceptions, name)("le service a répondu autre chose")

    assert classify_tts_failure(exc) is TTSFailureKind.FUNCTIONAL
    assert is_network_unavailable(exc) is False


def _synthetic(exc_type: type[BaseException], message: str) -> BaseException:
    """Instancie une exception aiohttp sans dépendre de son constructeur interne.

    `ClientConnectorError` et `WSServerHandshakeError` exigent des objets
    internes (`ConnectionKey`, `RequestInfo`) dont la signature varie d'une
    version d'aiohttp à l'autre. Seul le type importe pour la classification.
    """
    exc = exc_type.__new__(exc_type)
    BaseException.__init__(exc, message)
    return exc


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("ClientConnectorError", TTSFailureKind.NETWORK_UNAVAILABLE),
        ("ClientConnectorCertificateError", TTSFailureKind.NETWORK_UNAVAILABLE),
        ("ClientProxyConnectionError", TTSFailureKind.NETWORK_UNAVAILABLE),
        ("ServerDisconnectedError", TTSFailureKind.NETWORK_UNAVAILABLE),
        ("ServerTimeoutError", TTSFailureKind.NETWORK_UNAVAILABLE),
        # Un proxy qui refuse le CONNECT répond en HTTP : le service TTS n'a
        # jamais été atteint, c'est bien une indisponibilité réseau.
        ("ClientHttpProxyError", TTSFailureKind.NETWORK_UNAVAILABLE),
        # 401/403 côté service : jeton ou API modifiés — doit rester bruyant.
        ("WSServerHandshakeError", TTSFailureKind.FUNCTIONAL),
        ("ClientResponseError", TTSFailureKind.FUNCTIONAL),
    ],
)
def test_aiohttp_failures_are_classified_by_origin(name: str, expected: TTSFailureKind):
    aiohttp = pytest.importorskip("aiohttp")
    exc_type = getattr(aiohttp, name, None)
    if exc_type is None:
        pytest.skip(f"aiohttp n'expose pas {name} dans cette version")

    assert classify_tts_failure(_synthetic(exc_type, "échec simulé")) is expected


def test_cause_chain_is_inspected():
    root = ConnectionRefusedError(61, "Connection refused")
    wrapper = RuntimeError("échec de la synthèse")
    wrapper.__cause__ = root

    assert classify_tts_failure(wrapper) is TTSFailureKind.NETWORK_UNAVAILABLE


def test_a_service_that_answered_wins_over_transport_noise():
    aiohttp = pytest.importorskip("aiohttp")
    handshake = _synthetic(aiohttp.WSServerHandshakeError, "403, message='Forbidden'")
    handshake.__context__ = ConnectionResetError("reset après réponse")

    assert classify_tts_failure(handshake) is TTSFailureKind.FUNCTIONAL


def test_unknown_failure_defaults_to_functional():
    assert classify_tts_failure(ValueError("voix inconnue")) is TTSFailureKind.FUNCTIONAL


def test_self_referencing_cause_chain_terminates():
    exc = RuntimeError("boucle")
    exc.__context__ = exc

    assert classify_tts_failure(exc) is TTSFailureKind.FUNCTIONAL


def test_describe_failure_names_the_kind_and_truncates():
    exc = ConnectionRefusedError("x" * 5000)

    described = describe_tts_failure(exc)

    assert described.startswith("network_unavailable · builtins.ConnectionRefusedError")
    assert len(described) < 500


def test_describe_failure_survives_a_broken_str():
    class Hostile(Exception):
        def __str__(self) -> str:
            raise RuntimeError("__str__ cassé")

    described = describe_tts_failure(Hostile())

    assert "Hostile" in described


# ── 9. Contrat de payload : les régressions audio doivent échouer ────────────


@pytest.mark.parametrize(
    ("payload", "container"),
    [
        (WAV_PAYLOAD, CONTAINER_WAV),
        (M4A_PAYLOAD, CONTAINER_M4A),
        (OGG_PAYLOAD, CONTAINER_OGG),
        (b"<html>503 Service Unavailable</html>" + bytes(2000), CONTAINER_UNKNOWN),
        (b"", CONTAINER_EMPTY),
    ],
    ids=["wav_kokoro", "m4a_macos", "ogg", "html_erreur", "vide"],
)
def test_invalid_payloads_are_rejected(payload: bytes, container: str):
    assert detect_container(payload) == container
    assert mp3_payload_violations(payload) != []
    with pytest.raises(AssertionError):
        assert_mp3_payload(payload)


def test_truncated_mp3_is_rejected():
    truncated = ID3_CHUNK[:100]

    assert detect_container(truncated) == CONTAINER_MP3
    assert mp3_payload_violations(truncated) == [
        f"taille {len(truncated)} octets < minimum attendu {MIN_MP3_BYTES}"
    ]
    with pytest.raises(AssertionError, match="minimum attendu"):
        assert_mp3_payload(truncated)


@pytest.mark.parametrize(
    "payload",
    [ID3_CHUNK, FRAME_CHUNK, EXPECTED_AUDIO],
    ids=["tag_id3", "synchro_mpeg", "flux_complet"],
)
def test_valid_mp3_payloads_are_accepted(payload: bytes):
    assert detect_container(payload) == CONTAINER_MP3
    assert mp3_payload_violations(payload) == []
    assert_mp3_payload(payload)


def test_describe_payload_reports_container_and_size():
    described = describe_payload(WAV_PAYLOAD)

    assert f"conteneur={CONTAINER_WAV}" in described
    assert f"taille={len(WAV_PAYLOAD)}" in described

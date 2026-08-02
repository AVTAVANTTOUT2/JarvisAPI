"""Scénario réseau réel du TTS Edge — hors de la suite standard.

`edge-tts` parle à `speech.platform.bing.com` par WebSocket. Ce fichier est le
**seul** endroit du dépôt qui sort réellement sur Internet, et il est
désélectionné par défaut (`addopts = -m "not external_network"`).

Exécution volontaire, sur une machine ou une CI qui a le réseau :

    pytest -m external_network
    pytest -m external_network tests/test_tts_edge_external.py -v

Politique d'`skip` — délibérément étroite. Le service est sondé une fois par
module ; l'échec n'est ignoré que si `audio.tts_errors` le qualifie de
`network_unavailable` (DNS muet, refus TCP, délai dépassé, interception TLS,
proxy qui refuse le tunnel). Tout le reste échoue :

  - 401/403 au handshake (jeton ou API modifiés) ;
  - `NoAudioReceived` (voix disparue, paramètres refusés) ;
  - `UnknownResponse` / `UnexpectedResponse` (régression de parsing) ;
  - audio produit dans un autre format que MP3, ou trop court ;
  - catalogue de voix qui ne publie plus la voix masculine française.

Le contrat du payload est celui de `tests/tts_contract.py`, partagé avec les
tests unitaires mockés : les deux niveaux exigent exactement la même chose.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Awaitable
from typing import Any, TypeVar

import pytest

import config
from audio.tts_errors import describe_tts_failure, is_network_unavailable
from tests.tts_contract import (
    CONTAINER_MP3,
    assert_mp3_payload,
    describe_payload,
    detect_container,
)

tts_module = importlib.import_module("audio.tts")

pytestmark = [pytest.mark.external_network, pytest.mark.integration_tts]

T = TypeVar("T")

FRENCH_VOICE = "fr-FR-HenriNeural"
PROBE_TEXT = "Bonjour Monsieur."
SAMPLE_TEXT = "Bonjour Monsieur. Test de la voix française."
# Voix au format valide mais inexistante : le service répond, sans audio.
GHOST_VOICE = "fr-FR-VoixQuiNExistePasNeural"
NETWORK_TIMEOUT_SEC = 60


async def _collect_edge_audio(engine: Any, text: str, voice: str) -> bytes:
    """Appelle réellement Edge et laisse remonter l'exception d'origine."""
    original_voice = config.TTS_VOICE
    config.TTS_VOICE = voice
    try:
        chunks = [chunk async for chunk in engine._edge_audio_chunks(text)]
    finally:
        config.TTS_VOICE = original_voice
    return b"".join(chunks)


async def _await_or_skip(awaitable: Awaitable[T], *, what: str) -> T:
    """Ignore le test uniquement si le service est identifié comme injoignable."""
    try:
        return await asyncio.wait_for(awaitable, timeout=NETWORK_TIMEOUT_SEC)
    except Exception as exc:
        if is_network_unavailable(exc):
            pytest.skip(f"Edge injoignable pendant « {what} » : {describe_tts_failure(exc)}")
        raise


@pytest.fixture(scope="module")
def edge_engine() -> Any:
    """Moteur Edge réel ; ignore le module si `edge-tts` n'est pas installé."""
    pytest.importorskip("edge_tts", reason="edge-tts non installé (`pip install edge-tts`)")
    engine = tts_module.TTSEngine()
    assert engine.available is True
    assert engine.get_backend_name() == "edge"
    return engine


@pytest.fixture(scope="module")
def reachable_edge(edge_engine: Any) -> Any:
    """Sonde le service une seule fois par module.

    Injoignable → tout le module est ignoré, motif explicite. Échec
    fonctionnel → l'exception remonte et le module échoue : c'est le signal
    qu'on veut voir passer en CI réseau.
    """

    async def _probe() -> bytes:
        return await _collect_edge_audio(edge_engine, PROBE_TEXT, FRENCH_VOICE)

    try:
        audio = asyncio.run(asyncio.wait_for(_probe(), timeout=NETWORK_TIMEOUT_SEC))
    except Exception as exc:
        if is_network_unavailable(exc):
            pytest.skip(f"Edge injoignable (sonde initiale) : {describe_tts_failure(exc)}")
        raise
    assert_mp3_payload(audio, source="sonde Edge")
    return edge_engine


def test_edge_returns_a_french_mp3(reachable_edge: Any):
    audio = asyncio.run(_collect_edge_audio(reachable_edge, SAMPLE_TEXT, FRENCH_VOICE))

    assert_mp3_payload(audio, source=f"Edge {FRENCH_VOICE}")
    assert detect_container(audio) == CONTAINER_MP3, describe_payload(audio)


async def test_public_synthesize_returns_playable_mp3(reachable_edge: Any, monkeypatch):
    """Le chemin public avale ses erreurs : un retour vide est ici un défaut."""
    monkeypatch.setattr(config, "TTS_VOICE", FRENCH_VOICE)

    audio = await _await_or_skip(
        reachable_edge.synthesize(SAMPLE_TEXT, emotion="warm"), what="synthesize"
    )

    assert audio, "service joignable mais `synthesize()` n'a renvoyé aucun octet"
    assert_mp3_payload(audio, source="TTSEngine.synthesize")


async def test_streaming_yields_chunks_forming_a_valid_mp3(reachable_edge: Any, monkeypatch):
    monkeypatch.setattr(config, "TTS_VOICE", FRENCH_VOICE)

    async def _collect() -> list[bytes]:
        return [chunk async for chunk in reachable_edge.synthesize_stream(SAMPLE_TEXT)]

    chunks = await _await_or_skip(_collect(), what="synthesize_stream")

    assert chunks, "service joignable mais aucun chunk audio streamé"
    assert all(chunks), "chunk vide transmis au client"
    assert_mp3_payload(b"".join(chunks), source="TTSEngine.synthesize_stream")


async def test_french_male_voice_is_still_published(reachable_edge: Any):
    """Catalogue Edge : Henri doit toujours exister, et être masculin."""
    edge_tts = importlib.import_module("edge_tts")

    voices = await _await_or_skip(edge_tts.list_voices(), what="list_voices")

    henri = [voice for voice in voices if voice.get("ShortName") == FRENCH_VOICE]
    assert henri, f"{FRENCH_VOICE} absent du catalogue Edge ({len(voices)} voix listées)"
    assert henri[0].get("Gender") == "Male"
    assert henri[0].get("Locale") == "fr-FR"


def test_nonexistent_voice_fails_and_is_never_skipped(reachable_edge: Any):
    """Preuve en conditions réelles : un défaut fonctionnel reste un échec.

    Le service répond mais ne produit aucun audio. `audio.tts_errors` doit le
    qualifier de fonctionnel, donc non ignorable — sinon une voix supprimée par
    Microsoft passerait inaperçue.
    """
    with pytest.raises(Exception) as caught:  # noqa: PT011 - le type exact dépend d'edge-tts
        asyncio.run(_collect_edge_audio(reachable_edge, PROBE_TEXT, GHOST_VOICE))

    detail = describe_tts_failure(caught.value)
    if is_network_unavailable(caught.value):
        pytest.skip(f"réseau perdu pendant le scénario : {detail}")
    assert detail.startswith("functional"), detail

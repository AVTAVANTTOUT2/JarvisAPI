"""Diffusion réelle et annulation du backend Qwen3 — moteur local, sans réseau.

Ces scénarios font parler le vrai moteur. Ils portent donc le marqueur
``integration_tts`` : ils restent dans la suite hors ligne (aucune connexion
sortante), mais sont exclus par ``-m "not integration_tts"`` quand on ne veut
que l'unitaire.

Ce qu'ils vérifient ne peut pas l'être avec un double : qu'un fragment arrive
**avant** la fin de la synthèse. Un fournisseur qui accumulerait tout en
mémoire puis rendrait un seul bloc satisferait le contrat de type et
échouerait ici — c'est exactement la régression qu'un moteur sans diffusion
native rendrait invisible.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from jarvis.audio.tts import create_local_tts_provider, load_tts_settings

pytestmark = pytest.mark.integration_tts

PROJECT_ROOT = Path(__file__).resolve().parent.parent

COURTE = "Bonjour Monsieur."
TROIS_PHRASES = (
    "Bonjour Monsieur. Tous les systèmes sont opérationnels. "
    "Je reste à votre disposition."
)
LONGUE = (
    "Lille, dix-huit degrés, ciel couvert. Une averse est possible en fin "
    "d'après-midi. Je vous recommande de prendre un parapluie. Vos trois "
    "premières réunions sont confirmées, et la dernière attend toujours une "
    "réponse de votre interlocuteur."
)


def _model_installed() -> bool:
    settings = load_tts_settings()
    try:
        from native_audio.qwen3_local import resolve_model_dir

        resolve_model_dir(settings.model_path)
        return True
    except Exception:
        return False


needs_model = pytest.mark.skipif(
    not _model_installed(),
    reason="poids Qwen3 absents — python scripts/download_tts_model.py",
)


@pytest.fixture(scope="module")
def loop():
    """Une seule boucle pour tout le module.

    Le sidecar est un sous-processus asyncio : ses flux sont attachés à la
    boucle qui l'a démarré. Un ``asyncio.run()`` par test en créerait une
    nouvelle à chaque fois et la lecture échouerait sur « attached to a
    different loop » — sans rien dire du moteur lui-même.
    """
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    yield new_loop
    new_loop.close()
    asyncio.set_event_loop(None)


@pytest.fixture(scope="module")
def provider(loop):
    """Un seul moteur chargé pour tout le module — comme en production."""
    settings = replace(load_tts_settings(), provider="qwen3_local")
    prov = create_local_tts_provider(settings)
    loop.run_until_complete(prov.warmup())
    yield prov
    loop.run_until_complete(prov.close())


async def _collect(provider, text: str, request_id: str) -> dict:
    """Consomme un énoncé en notant la chronologie réelle des fragments."""
    started = time.perf_counter()
    chunks: list[tuple[float, int, bool]] = []
    async for chunk in provider.stream(
        text, request_id=request_id, utterance_id=request_id
    ):
        chunks.append(
            (
                (time.perf_counter() - started) * 1000.0,
                len(chunk.data),
                chunk.is_final,
            )
        )
    total_ms = (time.perf_counter() - started) * 1000.0
    return {
        "chunk_count": len(chunks),
        "first_chunk_ms": round(chunks[0][0], 1) if chunks else None,
        "first_chunk_samples": chunks[0][1] // 2 if chunks else 0,
        "last_chunk_ms": round(chunks[-1][0], 1) if chunks else None,
        "is_final_received": bool(chunks and chunks[-1][2]),
        "total_ms": round(total_ms, 1),
    }


@needs_model
@pytest.mark.parametrize(
    "label,text,min_chunks",
    [("courte", COURTE, 1), ("trois_phrases", TROIS_PHRASES, 2), ("longue", LONGUE, 3)],
)
def test_streaming_delivers_before_synthesis_ends(provider, loop, label, text, min_chunks):
    """Le premier son part avant la fin de la synthèse, et l'ordre est tenu."""
    report = loop.run_until_complete(_collect(provider, text, f"stream-{label}"))

    assert report["chunk_count"] >= min_chunks, report
    assert report["is_final_received"] is True, report
    assert report["first_chunk_samples"] > 0, report

    # Le cœur du test : sur un énoncé multi-fragments, le premier doit arriver
    # avant le dernier. Une synthèse rendue d'un bloc les collerait.
    if report["chunk_count"] > 1:
        assert report["first_chunk_ms"] < report["last_chunk_ms"], report

    # « Nettement avant la fin » n'a de sens qu'au-delà de quelques fragments.
    # Sur « Bonjour Monsieur. », le premier fragment *est* l'essentiel de la
    # synthèse : exiger qu'il tombe avant 75 % du total y punirait une phrase
    # courte au lieu de mesurer la diffusion.
    if report["chunk_count"] >= 4:
        assert report["first_chunk_ms"] < report["total_ms"] * 0.75, report

    # Trace lisible en cas d'échec, et relevé pour le rapport de migration.
    print(f"[qwen3-streaming] {label}: {json.dumps(report)}")


@needs_model
def test_cancel_after_first_chunk_stops_delivery(provider, loop):
    """Après annulation, plus aucun fragment n'est livré — pas de son tardif."""
    request_id = "stream-cancel"

    async def scenario() -> tuple[int, int]:
        delivered = 0
        stream = provider.stream(
            LONGUE, request_id=request_id, utterance_id=request_id
        )
        async for _chunk in stream:
            delivered += 1
            if delivered == 1:
                await provider.cancel(request_id)
        # Une seconde requête ne doit pas hériter de l'audio de la précédente.
        after = await _collect(provider, COURTE, "stream-after-cancel")
        return delivered, after["chunk_count"]

    delivered, after_chunks = loop.run_until_complete(scenario())

    # L'annulation prend effet à la frontière d'un fragment : on tolère celui
    # déjà en vol, jamais la suite complète de l'énoncé long.
    assert delivered <= 3, f"annulation sans effet : {delivered} fragments livrés"
    assert after_chunks >= 1, "le pipeline ne s'est pas réarmé après annulation"


@needs_model
def test_two_successive_requests_stay_independent(provider, loop):
    """Deux tours de parole enchaînés : aucun mélange, chacun sa fin."""
    first = loop.run_until_complete(_collect(provider, COURTE, "stream-seq-1"))
    second = loop.run_until_complete(_collect(provider, TROIS_PHRASES, "stream-seq-2"))

    assert first["is_final_received"] and second["is_final_received"]
    assert first["chunk_count"] >= 1 and second["chunk_count"] >= 1
    # La seconde requête est plus longue : elle doit produire plus d'audio.
    assert second["total_ms"] > first["total_ms"]


@needs_model
def test_engine_runs_without_any_network(provider, loop):
    """Le moteur parle avec les connexions sortantes refusées.

    La suite entière tourne déjà derrière un garde-fou réseau (`conftest.py`) :
    ce test le rend explicite pour le moteur vocal, puisque c'est la propriété
    que l'utilisateur a demandée en premier.
    """
    assert provider.info().offline is True
    report = loop.run_until_complete(_collect(provider, COURTE, "stream-offline"))
    assert report["chunk_count"] >= 1 and report["is_final_received"]


@needs_model
def test_private_voice_never_reaches_the_command_line(provider):
    """Ni le transcript ni son contenu ne doivent être visibles dans `ps`.

    Le sidecar reçoit le **répertoire** du profil et lit les fichiers lui-même.
    L'ancien backend passait `--ref-text` avec le transcript complet, donc plus
    de trois cents caractères de voix privée lisibles par tout processus de la
    machine.
    """
    command = " ".join(provider._client._command)  # noqa: SLF001 - contrat interne
    transcript = (PROJECT_ROOT / "voices" / "jarvis-fr" / "transcript.txt")

    assert "--ref-text" not in command
    if transcript.is_file():
        extrait = transcript.read_text(encoding="utf-8").strip()[:40]
        assert extrait and extrait not in command

"""Contrat de parole : ce que JARVIS prononce, scénario par scénario.

Ces tests traversent l'adaptateur vocal réel (``_process_voice_fast``), donc le
même chemin que le daemon local, la page ``/voice``, les mains-libres et le
mobile. Ils fixent le nombre d'énoncés autant que leur contenu : le défaut
corrigé n'était pas une mauvaise formulation mais **deux producteurs de parole
empilés sur un seul tour**.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from api.voice_processing import _process_voice_fast
from jarvis.voice import VoiceUtteranceKind, apply_address_policy, reset_voice_sessions


@pytest.fixture(autouse=True)
def _isolated_voice_state():
    reset_voice_sessions()
    yield
    reset_voice_sessions()


def _canonical(text: str, **extra):
    """Résultat du moteur conversationnel canonique."""
    return {
        "text": text,
        "emotion": "neutral",
        "agent": "info",
        "model": "test",
        "cost": 0.0,
        "action": None,
        "action_result": None,
        **extra,
    }


async def _turn(transcript: str, canonical: dict | None = None) -> dict:
    """Joue un tour vocal complet en neutralisant les effets de bord."""
    with (
        patch(
            "api.voice_processing._process_message_internal",
            AsyncMock(return_value=canonical if canonical is not None else _canonical("")),
        ),
        patch("api.voice_processing._persist_voice_messages_async") as persist,
        patch("api.voice_processing._broadcast_voice_debug", AsyncMock()),
        patch("api.voice_processing._save_voice_debug_trace", return_value=None),
        patch("api.voice_fastpath._persist_voice_messages_async"),
        patch(
            "api.voice_cognitive.maybe_handle_cognitive_voice",
            AsyncMock(return_value=None),
        ),
        patch("app.fitness.voice.maybe_handle_fitness_voice", return_value=None),
    ):
        result = await _process_voice_fast(transcript, 7)
        await asyncio.sleep(0)
        result["__persist_calls__"] = persist.call_count
    return result


# ── Tour conversationnel normal ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_normal_turn_speaks_exactly_the_answer() -> None:
    result = await _turn(
        "quel temps fait-il à Lille",
        _canonical("Il fait 18 degrés à Lille."),
    )
    assert result["text"] == "Il fait 18 degrés à Lille."


@pytest.mark.asyncio
async def test_normal_turn_never_keeps_a_trailing_honorific() -> None:
    """Le prompt peut échouer ; le filtre déterministe, non."""
    result = await _turn(
        "quel temps fait-il à Lille",
        _canonical("Il fait 18 degrés à Lille, Monsieur."),
    )
    assert result["text"] == "Il fait 18 degrés à Lille."


@pytest.mark.asyncio
async def test_normal_turn_never_keeps_a_hollow_preamble() -> None:
    result = await _turn(
        "lance l'analyse",
        _canonical("Bien, Monsieur. Je lance l'analyse."),
    )
    assert result["text"] == "Je lance l'analyse."


@pytest.mark.asyncio
async def test_turn_started_signal_is_awaited_but_never_speaks() -> None:
    """Le rappel d'état ne doit ni retarder la réponse, ni produire d'audio."""
    signals: list[str] = []

    async def _signal() -> None:
        signals.append("started")

    with (
        patch(
            "api.voice_processing._process_message_internal",
            AsyncMock(return_value=_canonical("Réponse finale.")),
        ),
        patch("api.voice_processing._persist_voice_messages_async"),
        patch("api.voice_processing._broadcast_voice_debug", AsyncMock()),
        patch("api.voice_processing._save_voice_debug_trace", return_value=None),
        patch(
            "api.voice_cognitive.maybe_handle_cognitive_voice",
            AsyncMock(return_value=None),
        ),
        patch("app.fitness.voice.maybe_handle_fitness_voice", return_value=None),
    ):
        result = await _process_voice_fast(
            "explique-moi la météo", 7, on_canonical_turn_started=_signal,
        )

    assert result["text"] == "Réponse finale."
    assert signals == ["started"]
    # Aucune tâche orpheline : le signal est récolté avant le retour.
    assert not [
        task for task in asyncio.all_tasks()
        if task.get_name() == "voice-turn-started-signal"
    ]


# ── Fast-paths déterministes ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hail_answers_without_honorific() -> None:
    result = await _turn("Jarvis ?")
    assert result["text"] == "Je vous écoute."
    assert result["model"] == "hail"


@pytest.mark.asyncio
async def test_stop_during_playback_produces_no_new_speech() -> None:
    """Répondre « Bien. » à « stop » contredit la demande.

    La commande coupe la lecture et rend la main ; l'absence de réponse *est* la
    réponse. Aucun tour d'assistant vide n'est écrit dans l'historique.
    """
    result = await _turn("stop")
    assert result["text"] == ""
    assert result["model"] == "control"
    assert result["__persist_calls__"] == 0


@pytest.mark.asyncio
async def test_cancel_command_confirms_without_honorific() -> None:
    result = await _turn("annule")
    assert result["text"] == "C'est annulé."


@pytest.mark.asyncio
async def test_empty_model_answer_is_reported_plainly() -> None:
    result = await _turn("dis-moi quelque chose", _canonical(""))
    assert result["text"] == "Je n'ai pas obtenu de réponse."


# ── Frontières de session ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sleep_is_a_session_boundary_and_stays_sober() -> None:
    result = await _turn(
        "mets-toi en veille",
        _canonical(
            "Je me mets en veille.",
            action={"type": "sleep"},
        ),
    )
    assert result["text"] == "Je me mets en veille."


@pytest.mark.asyncio
async def test_wake_may_greet_once_then_never_again() -> None:
    """Une ouverture de session garde l'honorifique — une seule fois."""
    first = await _turn(
        "réveille-toi",
        _canonical("Bonjour Monsieur.", action={"type": "wake"}),
    )
    second = await _turn(
        "réveille-toi",
        _canonical("Bonjour Monsieur.", action={"type": "wake"}),
    )
    assert first["text"] == "Bonjour Monsieur."
    assert "Monsieur" not in second["text"]


@pytest.mark.asyncio
async def test_every_wake_word_does_not_reopen_the_budget() -> None:
    """Un réveil n'est pas une nouvelle session : le budget reste consommé."""
    await _turn("réveille-toi", _canonical("Bonjour Monsieur.", action={"type": "wake"}))
    ordinary = await _turn("et la météo", _canonical("Il fait 18 degrés, Monsieur."))
    assert ordinary["text"] == "Il fait 18 degrés."


# ── Travaux longs ───────────────────────────────────────────────────────────


def test_long_job_acknowledgement_is_sober_and_singular() -> None:
    """Un accusé de progression annonce un travail réellement accepté.

    Il ne remplace pas l'accusé anticipé supprimé : celui-ci couvrait le temps
    de premier jeton d'un LLM, ce qui n'est pas un travail.
    """
    spoken = apply_address_policy(
        "Je lance l'analyse.", kind=VoiceUtteranceKind.PROGRESS,
    )
    assert spoken == "Je lance l'analyse."
    assert "Monsieur" not in spoken
    assert not spoken.lower().startswith("bien")

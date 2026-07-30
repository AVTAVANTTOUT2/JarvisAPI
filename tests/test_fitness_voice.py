"""Parsing vocal fitness, branchement STT et garanties de non-interception."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.fitness.voice import FitnessVoiceParser


@pytest.mark.parametrize(
    ("transcript", "method", "endpoint", "expected"),
    [
        (
            "Jarvis, note ma séance jambes",
            "POST",
            "/api/fitness/workouts",
            {"type": "jambes"},
        ),
        (
            "Note ma séance dos",
            "POST",
            "/api/fitness/workouts",
            {"type": "tirage"},
        ),
        (
            "Jarvis, j'ai mangé une salade au poulet",
            "POST",
            "/api/fitness/meals",
            {"description": "une salade au poulet", "calories_estimate": None},
        ),
        (
            "Jarvis, j'ai bu 25 cl d'eau",
            "POST",
            "/api/fitness/water",
            {"amount_ml": 250},
        ),
        (
            "Jarvis, j'ai bu un verre d'eau",
            "POST",
            "/api/fitness/water",
            {"amount_ml": 250},
        ),
        (
            "Jarvis, j'ai bu une bouteille d'eau",
            "POST",
            "/api/fitness/water",
            {"amount_ml": 500},
        ),
        (
            "Jarvis, j'ai bu un litre d'eau",
            "POST",
            "/api/fitness/water",
            {"amount_ml": 1000},
        ),
        (
            "Jarvis, mon bien-être est à 8 aujourd'hui",
            "POST",
            "/api/fitness/wellbeing",
            {"rating": 8},
        ),
        (
            "Jarvis, résume ma journée",
            "GET",
            "/api/fitness/summary/today",
            {},
        ),
        (
            "Jarvis, comment je me porte aujourd'hui",
            "GET",
            "/api/fitness/summary/today",
            {},
        ),
    ],
)
def test_mocked_stt_maps_high_confidence_intent_to_endpoint(
    transcript: str,
    method: str,
    endpoint: str,
    expected: dict[str, object],
) -> None:
    mocked_stt = Mock(return_value=transcript)
    parser = FitnessVoiceParser()

    intent = parser.parse(
        mocked_stt(b"fake-audio"),
        conversation_id=42,
        today=date(2026, 7, 30),
    )

    mocked_stt.assert_called_once_with(b"fake-audio")
    assert intent is not None
    assert intent.method == method
    assert intent.endpoint == endpoint
    assert intent.payload["date"] == "2026-07-30" if method == "POST" else True
    for key, value in expected.items():
        assert intent.payload[key] == value


def test_ambiguous_water_asks_for_quantity_without_guessing() -> None:
    intent = FitnessVoiceParser().parse(
        "Jarvis, j'ai bu de l'eau",
        conversation_id=1,
        today=date(2026, 7, 30),
    )

    assert intent is not None
    assert intent.endpoint is None
    assert "quantité" in intent.confirmation.lower()


def test_free_text_is_logged_only_inside_explicit_wellbeing_context() -> None:
    parser = FitnessVoiceParser()

    assert (
        parser.parse(
            "La journée était dense mais satisfaisante",
            conversation_id=7,
            today=date(2026, 7, 30),
        )
        is None
    )

    prompt = parser.parse(
        "Ouvre mon journal de bien-être",
        conversation_id=7,
        today=date(2026, 7, 30),
    )
    journal = parser.parse(
        "La journée était dense mais satisfaisante",
        conversation_id=7,
        today=date(2026, 7, 30),
    )

    assert prompt is not None and prompt.endpoint is None
    assert journal is not None
    assert journal.endpoint == "/api/fitness/wellbeing"
    assert (
        journal.payload["journal_text"] == "La journée était dense mais satisfaisante"
    )


@pytest.mark.parametrize(
    "phrase",
    [
        "J'ai bu un café avec Marc hier",
        "J'ai mangé avec Marc hier",
        "Peux-tu me parler de natation ?",
        "Le bien-être au travail est important",
        "Quelle quantité d'eau dois-je boire ?",
        "Résume la journée de Marc",
        "J'ai poussé la porte du garage",
        "On se fait un full body demain ?",
        "J'ai bu de très belles paroles dans ce livre",
    ],
)
def test_parser_does_not_intercept_non_fitness_phrases(phrase: str) -> None:
    assert (
        FitnessVoiceParser().parse(
            phrase,
            conversation_id=12,
            today=date(2026, 7, 30),
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase",
    [
        "J'ai bu un café avec Marc hier",
        "J'ai mangé avec Marc hier",
        "Peux-tu me parler de natation ?",
        "Quelle quantité d'eau dois-je boire ?",
        "Résume la journée de Marc",
    ],
)
async def test_voice_pipeline_fails_open_to_cognitive_routing(
    monkeypatch: pytest.MonkeyPatch,
    phrase: str,
) -> None:
    from api import voice_cognitive
    from api.voice_processing import _process_voice_fast

    routed = Mock()

    async def fake_cognitive(*args, **kwargs):
        routed(*args, **kwargs)
        return {"text": "ROUTAGE LLM", "action": None}

    monkeypatch.setattr(voice_cognitive, "maybe_handle_cognitive_voice", fake_cognitive)

    result = await _process_voice_fast(phrase, conversation_id=99)

    assert result["text"] == "ROUTAGE LLM"
    routed.assert_called_once()


@pytest.mark.asyncio
async def test_voice_pipeline_intercepts_explicit_fitness_before_llm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "voice-fitness.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)

    from database import init_db

    init_db()

    from api import voice_cognitive
    from api.voice_processing import _process_voice_fast

    async def should_not_run(*args, **kwargs):
        raise AssertionError(
            "Le routage LLM ne doit pas recevoir cette commande explicite"
        )

    monkeypatch.setattr(voice_cognitive, "maybe_handle_cognitive_voice", should_not_run)

    result = await _process_voice_fast(
        "Jarvis, note ma séance jambes", conversation_id=101
    )

    assert result["text"] == "Séance jambes enregistrée."
    assert result["action"]["endpoint"] == "/api/fitness/workouts"

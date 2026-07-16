"""Préambule cognitif vocal — ack immédiat, briefing variants, contrôle barge-in."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from api.voice_cognitive import _detect_briefing_variant, maybe_handle_cognitive_voice  # noqa: E402
from api.voice_processing import _match_voice_control  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "voice.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


# ── Commandes de contrôle barge-in (déterministes, zéro LLM) ─


def test_voice_control_stop_commands():
    assert _match_voice_control("Arrête") == "Bien, Monsieur."
    assert _match_voice_control("stop !") == "Bien, Monsieur."
    assert _match_voice_control("tais-toi") == "Bien, Monsieur."


def test_voice_control_cancel():
    assert _match_voice_control("annule") == "C'est annulé, Monsieur."
    assert _match_voice_control("laisse tomber.") == "C'est annulé, Monsieur."


def test_voice_control_not_matched_in_sentences():
    # « arrête » au milieu d'une vraie phrase ne doit PAS court-circuiter
    assert _match_voice_control("arrête de me proposer des tâches chaque matin s'il te plaît") is None
    assert _match_voice_control("Quel temps fait-il ?") is None
    assert _match_voice_control("") is None


# ── Variantes de briefing vocal ──────────────────────────────


def test_briefing_variant_detection():
    assert _detect_briefing_variant("fais-moi mon briefing")["kind"] == "morning"
    assert _detect_briefing_variant("résumé du soir")["kind"] == "evening"
    assert _detect_briefing_variant("qu'est-ce qui a changé depuis ce matin ?")["kind"] == "delta"
    assert _detect_briefing_variant("seulement les urgences")["filter_priority"] == "critique"
    assert _detect_briefing_variant("fais-moi la version courte")["voice_only"] is True
    assert _detect_briefing_variant("seulement le travail")["work_only"] is True


# ── Préambule cognitif ───────────────────────────────────────


def test_simple_voice_question_continues_to_flash(tmp_db):
    """Question simple → __continue__ (le pipeline Flash classique répond)."""
    result = asyncio.run(
        maybe_handle_cognitive_voice("Quelle heure est-il ?", 1, t0=0.0)
    )
    assert result is not None
    assert result.get("__continue__") is True
    assert result["intent"].execution_type in ("answer", "tool")


def test_voice_tech_delegates_and_acks(tmp_db, monkeypatch):
    """Demande technique vocale → proposition (pas d'auto-start) + invite « lance »."""
    import config

    monkeypatch.setattr(config, "CURSOR_DELEGATION_ENABLED", True)

    fake_job = {
        "job_id": "job-test-123",
        "prompt_template": "bug_fix",
        "template_version": "2.0.0",
        "status": "awaiting_confirmation",
    }
    enqueue_mock = AsyncMock(return_value=fake_job)

    with patch("integrations.cursor_delegation.cursor_delegation") as svc:
        svc.enqueue = enqueue_mock
        result = asyncio.run(
            maybe_handle_cognitive_voice(
                "Corrige le bug de connexion Android dans le projet", 1, t0=0.0
            )
        )

    assert result is not None and not result.get("__continue__")
    assert "lance" in result["text"].lower()
    assert result["action"]["type"] == "cursor_propose"
    assert result["action"]["job_id"] == "job-test-123"
    enqueue_mock.assert_awaited_once()
    kwargs = enqueue_mock.await_args.kwargs
    assert kwargs.get("auto_start") is False
    assert kwargs.get("require_confirmation") is True
    assert len(result["text"]) < 250


def test_voice_tech_cursor_failure_honest_message(tmp_db, monkeypatch):
    """Échec d'enqueue → message honnête, pas de fausse promesse."""
    import config

    monkeypatch.setattr(config, "CURSOR_DELEGATION_ENABLED", True)
    enqueue_mock = AsyncMock(side_effect=RuntimeError("CLI non authentifié"))

    with patch("integrations.cursor_delegation.cursor_delegation") as svc:
        svc.enqueue = enqueue_mock
        result = asyncio.run(
            maybe_handle_cognitive_voice(
                "Corrige le bug de connexion Android dans le projet", 1, t0=0.0
            )
        )

    assert result is not None and not result.get("__continue__")
    assert "je ne peux pas" in result["text"].lower()
    assert "cursor" in result["text"].lower()
    assert result["action"] is None


def test_voice_briefing_returns_voice_text(tmp_db):
    """« Fais-moi mon briefing » → version vocale courte du moteur."""
    from agents.briefing_engine import StructuredBriefing

    fake = StructuredBriefing(
        kind="morning", generated_at="now", items=[],
        full_text="Version écran complète très longue…",
        voice_text="Bonjour Monsieur. Trois points ce matin.",
    )
    with patch(
        "agents.briefing_engine.generate_structured_briefing",
        new=AsyncMock(return_value=fake),
    ):
        result = asyncio.run(
            maybe_handle_cognitive_voice("Fais-moi mon briefing", 1, t0=0.0)
        )

    assert result is not None and not result.get("__continue__")
    assert result["text"] == "Bonjour Monsieur. Trois points ce matin."


def test_voice_heavy_ack_then_background_main(tmp_db):
    """Réflexion lourde → ack Flash immédiat + suivi Main en tâche de fond."""
    chat_mock = AsyncMock(return_value={"content": "PLAN COMPLET", "model": "main",
                                        "tokens_in": 10, "tokens_out": 20, "cost": 0.01})

    async def _run():
        with patch("llm.chat", new=chat_mock):
            result = await maybe_handle_cognitive_voice(
                "Organise ma journée en fonction de mes priorités", 1, t0=0.0
            )
            assert result is not None and not result.get("__continue__")
            # Ack immédiat — le plan n'est PAS dans la réponse vocale
            assert "PLAN COMPLET" not in result["text"]
            assert result["latency_ms"] is not None
            # Laisse le suivi de fond s'exécuter
            await asyncio.sleep(0.3)
            return result

    result = asyncio.run(_run())
    assert chat_mock.await_count >= 1  # le plan Main a bien été généré en fond


def test_voice_stt_latency_recorded(tmp_db):
    result = asyncio.run(
        maybe_handle_cognitive_voice("Quelle heure est-il ?", 1, t0=0.0, stt_ms=420)
    )
    assert result["debug_trace"]["latency_stt_ms"] == 420

"""Contrats du Coach : un seul appel utile et aucun marqueur interne visible."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agents.coach import CoachAgent


def _result(text: str = "Analyse utile") -> dict:
    return {
        "response": text,
        "agent": "coach",
        "model": "main-model",
        "tokens_in": 10,
        "tokens_out": 20,
        "cost": 0.01,
        "emotion": "neutral",
    }


@pytest.mark.asyncio
async def test_non_voice_coaching_uses_one_main_call_without_preclassification():
    agent = CoachAgent()
    call = AsyncMock(return_value=_result())

    with patch.object(agent, "_call_claude", call), patch(
        "agents.coach.get_all_people", return_value=[]
    ), patch("agents.coach.get_active_patterns", return_value=[]), patch(
        "agents.coach.get_recent_moods", return_value=[]
    ):
        result = await agent.handle("Je dois changer de carrière", context={})

    assert result["response"] == "Analyse utile"
    call.assert_awaited_once()
    assert call.await_args.kwargs["voice_mode"] is False
    assert "model" not in call.await_args.kwargs
    assert not hasattr(agent, "_should_escalate")


@pytest.mark.asyncio
async def test_voice_coaching_keeps_the_voice_budget_path():
    agent = CoachAgent()
    call = AsyncMock(return_value=_result("Réponse courte"))

    with patch.object(agent, "_call_claude", call), patch(
        "agents.coach.get_all_people", return_value=[]
    ), patch("agents.coach.get_active_patterns", return_value=[]), patch(
        "agents.coach.get_recent_moods", return_value=[]
    ):
        await agent.handle("Aide-moi", context={"voice_mode": True})

    call.assert_awaited_once()
    assert call.await_args.kwargs["voice_mode"] is True


@pytest.mark.asyncio
async def test_stream_contract_has_no_dead_escalation_field():
    agent = CoachAgent()

    with patch.object(agent, "_call_coach", AsyncMock(return_value=_result())):
        events = [event async for event in agent.handle_stream("Aide-moi", context={})]

    done = events[-1]
    assert done["type"] == "done"
    assert "escalated" not in done


def test_coach_prompt_never_requests_internal_deep_analysis_tag():
    prompt = (
        Path(__file__).resolve().parents[1] / "prompts" / "coach.txt"
    ).read_text(encoding="utf-8")

    assert "[DEEP_ANALYSIS]" not in prompt
    assert "sans tag technique" in prompt

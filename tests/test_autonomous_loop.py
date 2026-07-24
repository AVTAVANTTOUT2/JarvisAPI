"""Tests du mode autonome /loop."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from agents.autonomous_loop import (
    _extract_action_from_llm,
    _prepare_action_for_loop,
    parse_loop_command,
    run_autonomous_loop,
)


class TestParseLoopCommand:
    def test_loop_with_task(self) -> None:
        assert parse_loop_command("/loop installe redis") == "installe redis"

    def test_loop_colon_syntax(self) -> None:
        assert parse_loop_command("/loop: corrige le bug") == "corrige le bug"

    def test_loop_empty(self) -> None:
        assert parse_loop_command("/loop") == ""

    def test_not_loop(self) -> None:
        assert parse_loop_command("bonjour") is None


class TestExtractActionFromLlm:
    def test_termine(self) -> None:
        action, text, done = _extract_action_from_llm("TERMINE")
        assert action is None
        assert done is True

    def test_action_fence(self) -> None:
        raw = 'Je liste les fichiers.\n```action\n{"type":"terminal","command":"ls"}\n```'
        action, text, done = _extract_action_from_llm(raw)
        assert action == {"type": "terminal", "command": "ls"}
        assert done is False
        assert "liste" in text.lower()

    def test_inline_json(self) -> None:
        raw = 'Go ```action {"type":"weather","city":"Lille"}```'
        action, _, done = _extract_action_from_llm(raw)
        assert action == {"type": "weather", "city": "Lille"}
        assert done is False


class TestPrepareActionForLoop:
    def test_terminal_is_never_auto_confirmed(self) -> None:
        action = _prepare_action_for_loop({"type": "terminal", "command": "rm file.txt"})
        assert "confirmed" not in action
        assert action["complex"] is True
        assert action["execution_origin"] == "autonomous_loop"

    def test_simple_terminal_is_not_auto_confirmed(self) -> None:
        action = _prepare_action_for_loop({
            "type": "terminal",
            "command": "pwd",
            "complex": False,
            "confirmed": True,
        })
        assert "confirmed" not in action
        assert action["complex"] is False
        assert action["execution_origin"] == "autonomous_loop"

    def test_non_terminal_action_keeps_autonomous_confirmation(self) -> None:
        action = _prepare_action_for_loop({"type": "weather", "city": "Lille"})
        assert action["confirmed"] is True


class TestRunAutonomousLoop:
    @pytest.mark.asyncio
    async def test_completes_after_termine(self) -> None:
        events: list[tuple[str, dict]] = []

        async def on_event(event_type: str, data: dict) -> None:
            events.append((event_type, data))

        llm_responses = [
            {
                "content": (
                    'Première étape.\n```action\n'
                    '{"type":"weather","city":"Lille"}\n```'
                ),
                "cost": 0.001,
            },
            {"content": "TERMINE", "cost": 0.001},
            {"content": "[neutral]\nMétéo récupérée.", "cost": 0.002},
        ]

        async def fake_chat(**kwargs):  # noqa: ANN003
            return llm_responses.pop(0)

        with patch("agents.autonomous_loop.llm.chat", side_effect=fake_chat):
            with patch("actions.execute_action", new_callable=AsyncMock, return_value={"ok": True, "message": "18°C couvert"}):
                result = await run_autonomous_loop(
                    "quel temps il fait à Lille",
                    conversation_id=None,
                    context={},
                    on_event=on_event,
                    unlimited=True,
                )

        assert result["final_status"] == "completed"
        assert result["step_count"] == 1
        assert result["total_llm_calls"] >= 2
        assert any(e[0] == "loop_started" for e in events)
        assert any(e[0] == "loop_done" for e in events)

    @pytest.mark.asyncio
    async def test_empty_task_immediate_fail(self) -> None:
        result = await run_autonomous_loop("", None, {}, unlimited=True)
        assert result["final_status"] == "failed"

    @pytest.mark.asyncio
    async def test_terminal_plan_pauses_for_human_confirmation(self) -> None:
        llm_response = {
            "content": (
                "Je prépare l'inspection.\n```action\n"
                '{"type":"terminal","command":"pwd"}\n```'
            ),
            "cost": 0.001,
        }
        plan_result = {
            "ok": True,
            "needs_confirmation": True,
            "commands": ["pwd"],
            "message": "confirmation requise",
        }
        with patch(
            "agents.autonomous_loop.llm.chat",
            new_callable=AsyncMock,
            return_value=llm_response,
        ) as chat:
            with patch(
                "actions.execute_action",
                new_callable=AsyncMock,
                return_value=plan_result,
            ):
                result = await run_autonomous_loop(
                    "inspecte le workspace",
                    None,
                    {},
                    unlimited=True,
                )

        assert result["final_status"] == "awaiting_confirmation"
        assert result["pending_action"]["type"] == "terminal"
        assert result["pending_action"]["execution_origin"] == "autonomous_loop"
        assert chat.await_count == 1

    @pytest.mark.asyncio
    async def test_respects_llm_call_limit(self) -> None:
        with patch("config.LOOP_UNLIMITED", False):
            with patch("config.LOOP_MAX_LLM_CALLS", 1):
                with patch("config.LOOP_MAX_STEPS", 10):
                    with patch(
                        "agents.autonomous_loop.llm.chat",
                        new_callable=AsyncMock,
                        return_value={"content": "TERMINE", "cost": 0.0},
                    ):
                        result = await run_autonomous_loop(
                            "tâche test",
                            None,
                            {},
                            unlimited=False,
                        )
        assert result["final_status"] in ("completed", "failed", "partial")

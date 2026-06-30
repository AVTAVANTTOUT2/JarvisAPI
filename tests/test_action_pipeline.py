"""Tests du pipeline d'actions et de la détection agentique."""

from __future__ import annotations

import pytest

from agents.display_text import finalize_assistant_display_text
from main import _extract_action_from_text, _is_agentic_action


class TestIsAgenticAction:
    def test_simple_terminal_is_not_agentic(self) -> None:
        assert _is_agentic_action({"type": "terminal", "command": "ls -la"}) is False

    def test_complex_terminal_is_agentic(self) -> None:
        assert _is_agentic_action({
            "type": "terminal",
            "command": "analyse data.csv",
            "complex": True,
        }) is True

    def test_weather_never_agentic(self) -> None:
        assert _is_agentic_action({"type": "weather", "complex": True}) is False


class TestExtractActionFromText:
    def test_standard_fence(self) -> None:
        text = 'Voici.\n```action\n{"type":"weather","city":"Lille"}\n```'
        action, clean = _extract_action_from_text(text)
        assert action == {"type": "weather", "city": "Lille"}
        assert "```" not in clean

    def test_fence_without_newline_after_action(self) -> None:
        text = '```action {"type":"open_app","app_name":"Safari"}```'
        action, clean = _extract_action_from_text(text)
        assert action == {"type": "open_app", "app_name": "Safari"}
        assert clean == ""

    def test_inline_json_fallback(self) -> None:
        text = 'OK {"type":"task","title":"Test"} fin'
        action, clean = _extract_action_from_text(text)
        assert action == {"type": "task", "title": "Test"}
        assert "{" not in clean


class TestFinalizeDisplayText:
    def test_strips_action_without_newline(self) -> None:
        raw = '```action {"type":"weather","city":"Lille"}```'
        assert finalize_assistant_display_text(raw) == ""

    def test_strips_standard_action_block(self) -> None:
        raw = (
            "[warm]\n"
            "Je regarde.\n"
            '```action\n{"type":"terminal","command":"ls","complex":true}\n```'
        )
        out = finalize_assistant_display_text(raw)
        assert "action" not in out.lower()
        assert "Je regarde." in out

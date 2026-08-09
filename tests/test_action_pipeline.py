"""Tests du pipeline d'actions et de la détection agentique."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Import display_text sans charger agents/__init__.py (dépendances lourdes)
_spec = importlib.util.spec_from_file_location(
    "display_text",
    Path(__file__).resolve().parents[1] / "agents" / "display_text.py",
)
_display = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_display)


class TestIsAgenticAction:
    def test_simple_terminal_is_not_agentic(self) -> None:
        from main import _is_agentic_action
        assert _is_agentic_action({"type": "terminal", "command": "ls -la"}) is False

    def test_unconfirmed_complex_terminal_is_not_agentic(self) -> None:
        from main import _is_agentic_action
        assert _is_agentic_action({
            "type": "terminal",
            "command": "analyse data.csv",
            "complex": True,
        }) is False

    def test_only_confirmed_server_plan_can_be_agentic(self) -> None:
        from main import _is_agentic_action
        assert _is_agentic_action({
            "type": "terminal",
            "command": "analyse data.csv",
            "complex": True,
            "confirmed": True,
            "shell_plan_id": "opaque-server-plan",
        }) is True


class TestShouldDeferAction:
    def test_defer_on_proposal_question(self) -> None:
        from api.chat_actions import _should_defer_action
        assert _should_defer_action(
            "Veux-tu que j'analyse ce fichier ?",
            {"type": "terminal", "command": "head data.csv"},
        ) is True

    def test_no_defer_on_direct_terminal_command(self) -> None:
        from api.chat_actions import _should_defer_action
        assert _should_defer_action(
            "J'exécute la commande.",
            {"type": "terminal", "command": "ls"},
        ) is False

    def test_sensitive_action_deferred_without_confirmation(self) -> None:
        from api.chat_actions import _should_defer_action
        assert _should_defer_action(
            "Événement créé.",
            {"type": "calendar_create", "summary": "RDV"},
        ) is True

    def test_sensitive_action_ignores_a_confirmed_flag_from_the_model(self) -> None:
        """Le champ `confirmed` d'un bloc ```action``` est écrit par le modèle.

        `_should_defer_action` ne reçoit que des actions fraîchement extraites
        d'une réponse : le chemin « proposition serveur confirmée » rend la
        main bien avant. Honorer ce champ laisserait donc une génération de
        texte lever le garde-fou censé la contenir.
        """
        from api.chat_actions import _should_defer_action
        assert _should_defer_action(
            "Événement créé.",
            {"type": "calendar_create", "summary": "RDV", "confirmed": True},
        ) is True


class TestExtractActionFromText:
    def test_standard_fence(self) -> None:
        from main import _extract_action_from_text
        text = 'Voici.\n```action\n{"type":"weather","city":"Lille"}\n```'
        action, clean = _extract_action_from_text(text)
        assert action == {"type": "weather", "city": "Lille"}
        assert "```" not in clean

    def test_fence_without_newline_after_action(self) -> None:
        from main import _extract_action_from_text
        text = '```action {"type":"open_app","app_name":"Safari"}```'
        action, clean = _extract_action_from_text(text)
        assert action == {"type": "open_app", "app_name": "Safari"}
        assert clean == ""

    def test_json_code_fence_is_not_an_action(self) -> None:
        from main import _extract_action_from_text
        text = 'Exemple : ```json\n{"type":"task","title":"démo"}\n```'
        action, clean = _extract_action_from_text(text)
        assert action is None
        assert clean == text


class TestFinalizeDisplayText:
    def test_strips_action_without_newline(self) -> None:
        raw = '```action {"type":"weather","city":"Lille"}```'
        assert _display.finalize_assistant_display_text(raw) == ""

    def test_sanitize_streaming_hides_partial_fence(self) -> None:
        raw = "Je regarde.\n```action\n{\"type\":\"terminal\""
        out = _display.sanitize_streaming_display(raw)
        assert "```" not in out
        assert "Je regarde." in out

"""Tests du routage cognitif Flash / Main / Cursor + garde-fou Ollama."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.cognitive.router import CognitiveRouter, route_request
from jarvis.cognitive.ollama_guard import (
    OLLAMA_ALLOWED_MODULES,
    OllamaPolicyError,
    assert_ollama_caller_allowed,
    ollama_reasoning_consumers,
)
from jarvis.cognitive.context_planner import plan_context
from jarvis.cognitive.capability_registry import get_capability_registry
from integrations.cursor_prompt_composer import parse_cursor_result
from integrations.cursor_cli import inspect_cursor_cli, resolve_cursor_agent_path
from integrations.contact_resolver import resolve_contact_query, _fold


def test_voice_simple_routes_to_flash():
    intent = route_request("Quel temps fait-il ?", interaction_mode="voice")
    assert intent.reasoning_model  # configured
    assert intent.execution_type in ("answer", "tool")
    assert intent.domain in ("info", "general", "productivity")


def test_voice_tech_routes_to_cursor_with_ack(monkeypatch):
    import config

    monkeypatch.setattr(config, "CURSOR_DELEGATION_ENABLED", True)
    intent = route_request(
        "Corrige le bug de connexion Android dans le projet",
        interaction_mode="voice",
    )
    assert intent.execution_type == "cursor"
    assert intent.voice_ack
    assert intent.prompt_model
    assert intent.template_id


def test_voice_strategy_ack_then_main():
    intent = route_request(
        "Organise ma journée en fonction de mes priorités",
        interaction_mode="voice",
    )
    assert intent.complexity == "heavy"
    assert intent.execution_type == "answer"
    assert intent.voice_ack
    assert intent.prompt_model


def test_text_code_routes_to_cursor(monkeypatch):
    import config

    monkeypatch.setattr(config, "CURSOR_DELEGATION_ENABLED", True)
    intent = route_request(
        "Crée la migration SQL et applique-la dans mon projet",
        interaction_mode="chat",
    )
    assert intent.execution_type == "cursor"


def test_tech_without_cursor_falls_back_to_honest_answer(monkeypatch):
    """Cursor indisponible → réponse conseil Main, jamais de fausse promesse."""
    import config

    monkeypatch.setattr(config, "CURSOR_DELEGATION_ENABLED", False)
    intent = route_request(
        "Corrige le bug de connexion Android dans le projet",
        interaction_mode="chat",
    )
    assert intent.execution_type == "answer"
    assert intent.domain == "dev"
    assert "indisponible" in intent.reason


def test_tech_explain_stays_on_deepseek():
    intent = route_request(
        "Explique-moi ce que est une migration SQL",
        interaction_mode="chat",
    )
    assert intent.execution_type == "answer"
    assert intent.domain == "dev_explain"


def test_deterministic_tool_calendar():
    intent = route_request("Mon agenda demain", interaction_mode="voice")
    assert intent.execution_type == "tool"
    assert intent.domain == "productivity"


def test_contacts_no_cursor():
    intent = route_request("Donne-moi le numéro de Thomas", interaction_mode="voice")
    assert intent.domain == "contacts"
    assert intent.execution_type == "tool"


def test_context_planner_contact_budget():
    intent = route_request("Appelle maman", interaction_mode="voice")
    planned = plan_context(
        intent,
        {"contact": "Maman — +33…", "user_facts": "NE PAS INJECTER", "horodatage": "now"},
    )
    keys = {s.key for s in planned.slices}
    assert "CONTACT" in keys or "TIME" in keys
    assert "FACTS" not in keys


def test_capability_registry_lists_cursor():
    reg = get_capability_registry()
    reg.refresh()
    names = {c["name"] for c in reg.list_all()}
    assert "cursor.delegate" in names
    assert "screen_watcher.vision" in names


def test_parse_cursor_result_completed():
    raw = """
some noise
JARVIS_CURSOR_RESULT_BEGIN
Verdict: COMPLETED
Root cause:
null pointer
Files changed:
a.py
Tests:
ok
JARVIS_CURSOR_RESULT_END
"""
    parsed = parse_cursor_result(raw)
    assert parsed["parsed"] is True
    assert parsed["verdict"] == "COMPLETED"


def test_parse_cursor_result_missing_markers():
    parsed = parse_cursor_result("no markers here")
    assert parsed["parsed"] is False
    assert parsed["verdict"] == "PARTIAL"


def test_ollama_allowlist_contains_screen_watcher():
    assert "scripts/screen_watcher.py" in OLLAMA_ALLOWED_MODULES
    assert "integrations/ollama_control.py" in OLLAMA_ALLOWED_MODULES


def test_ollama_reasoning_consumers_contract():
    """CONTRAT STRICT : zéro consommateur de raisonnement Ollama hors allowlist.

    Résultat attendu : `Ollama reasoning consumers = screen_watcher uniquement`
    (screen_watcher passe par integrations/ollama_client qui est de la
    plomberie ; tout autre fichier listé ici est une régression).
    """
    root = Path(__file__).resolve().parents[1]
    offenders = ollama_reasoning_consumers(root)
    assert offenders == [], (
        "Nouveaux chemins de raisonnement local détectés — Ollama est réservé "
        f"au Screen Watcher : {offenders}"
    )


def test_ollama_guard_blocks_foreign_caller():
    import inspect

    frames = inspect.stack()
    with pytest.raises(OllamaPolicyError):
        assert_ollama_caller_allowed(frames)


def test_ollama_guard_blocks_real_http_entrypoint():
    """Le VRAI point d'entrée HTTP refuse un appelant hors allowlist.

    Régression du bug historique : la pile commençait dans ollama_guard
    (module autorisé) → tout appel via ollama_http_request passait.
    """
    import asyncio

    from jarvis.cognitive.ollama_guard import ollama_http_request

    with pytest.raises(OllamaPolicyError):
        asyncio.run(ollama_http_request("GET", "http://localhost:11434/api/tags"))


def test_ollama_guard_allows_screen_watcher_style_caller(tmp_path):
    """Un frame screen_watcher.py dans la pile → autorisé."""
    import subprocess
    import sys

    script = tmp_path / "screen_watcher.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from jarvis.cognitive.ollama_guard import assert_ollama_caller_allowed\n"
        "print(assert_ollama_caller_allowed())\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert "screen_watcher" in result.stdout


def test_contact_fold_accents():
    assert _fold("Éléonore") == "eleonore"


def test_contact_resolver_empty():
    result = resolve_contact_query("")
    assert result["status"] == "not_found"


def test_cursor_cli_detection_smoke():
    # Ne suppose pas que Cursor est installe — verifie juste que inspect ne crash pas.
    info = inspect_cursor_cli()
    assert "available" in info.to_dict()
    # Sur la machine de dev actuelle, agent est typiquement present
    path = resolve_cursor_agent_path()
    if path:
        assert info.available is True

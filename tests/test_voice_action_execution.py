"""Régression : le pipeline interne doit exécuter les blocs ```action``` (Android vocal)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agents.display_text import strip_assistant_code_fences, strip_non_action_fences


def test_strip_non_action_fences_keeps_action_block():
    raw = (
        '[warm] J\'ouvre OBS.\n'
        '```action {"type":"open_app","name":"OBS"} ```\n'
        '```json\n{"x":1}\n```'
    )
    kept = strip_non_action_fences(raw)
    assert "```action" in kept
    assert '"type":"open_app"' in kept
    assert "```json" not in kept

    gone = strip_assistant_code_fences(raw)
    assert "```action" not in gone
    assert "J'ouvre OBS" in gone


@pytest.mark.asyncio
async def test_base_agent_preserves_action_fence_in_response():
    """Régression : _call_claude ne doit plus stripper ```action``` avant le pipeline."""
    from agents.info import InfoAgent

    agent = InfoAgent()
    raw = '[warm] J\'ouvre OBS. ```action {"type":"open_app","name":"OBS"} ```'
    with patch("llm.chat", AsyncMock(return_value={
        "content": raw,
        "model": "deepseek-v4-flash",
        "tokens_in": 1,
        "tokens_out": 1,
        "cost": 0.0,
    })), patch("agents.__init__.event_bus") as eb, patch("agents.__init__.save_message") as sm:
        eb.emit = AsyncMock()
        result = await agent._call_claude(
            "ouvre OBS",
            conversation_id=99,
            context={"__defer_persist": True, "voice_mode": True},
        )

    assert "```action" in result["response"]
    assert result["emotion"] == "warm"
    sm.assert_not_called()


@pytest.mark.asyncio
async def test_school_agent_preserves_open_app_action():
    """School ne doit plus stripper ```action``` via finalize_assistant_display_text."""
    from agents.school import SchoolAgent

    agent = SchoolAgent()
    raw = '[neutral] J\'ouvre Roblox. ```action {"type":"open_app","name":"Roblox"} ```'
    with patch("llm.chat", AsyncMock(return_value={
        "content": raw,
        "model": "deepseek-v4-flash",
        "tokens_in": 1,
        "tokens_out": 1,
        "cost": 0.0,
    })), patch("agents.__init__.event_bus") as eb, patch("agents.__init__.save_message"):
        eb.emit = AsyncMock()
        result = await agent.handle(
            "Ouvre Roblox",
            conversation_id=None,
            context={"__defer_persist": True, "voice_mode": True},
        )

    assert "```action" in result["response"]
    assert "Roblox" in result["response"]


@pytest.mark.asyncio
async def test_voice_confirmation_consumes_pending_action_without_llm(monkeypatch):
    import api.chat_actions as chat_actions
    from api.voice_support import _maybe_execute_pending_voice_action

    monkeypatch.setattr(chat_actions, "_pending_proposal", None)
    chat_actions._maybe_store_pending_proposal(
        {"type": "terminal", "shell_plan_id": "server-plan"},
        conversation_id=7,
    )
    execute = AsyncMock(return_value={"ok": True, "output": "done"})

    with patch("actions.execute_action", execute), patch(
        "api.voice_support._save_voice_messages"
    ):
        result = await _maybe_execute_pending_voice_action(
            "oui",
            7,
            started_at=0.0,
        )

    assert result is not None
    assert result["debug_trace"]["model"] == "pending_confirmation"
    execute.assert_awaited_once_with({
        "type": "terminal",
        "shell_plan_id": "server-plan",
        "confirmed": True,
    })


def test_computer_patterns_route_open_app_to_productivity():
    from agents.orchestrator import COMPUTER_PATTERNS, _match_any

    assert _match_any("Ouvre Roblox s'il te plaît.", COMPUTER_PATTERNS)
    assert _match_any("lance Safari", COMPUTER_PATTERNS)
    assert not _match_any("quel temps fait-il", COMPUTER_PATTERNS)


@pytest.mark.asyncio
async def test_process_message_internal_executes_open_app(monkeypatch, tmp_path):
    import config
    import database
    from api.chat_processing import _process_message_internal
    from database import create_conversation, init_db

    db_path = tmp_path / "actions.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    init_db()
    conv_id = create_conversation(agent="android_voice")

    llm_response = (
        '[warm] J\'ouvre OBS tout de suite. '
        '```action {"type":"open_app","name":"OBS"} ```'
    )

    mock_handle = AsyncMock(
        return_value={
            "response": llm_response,
            "agent": "info",
            "model": "deepseek-v4-flash",
            "tokens_in": 10,
            "tokens_out": 20,
            "cost": 0.001,
            "emotion": "warm",
            "category": "INFO",
        }
    )
    mock_exec = AsyncMock(
        return_value={"ok": True, "command": "open -a OBS", "message": "OBS ouvert."}
    )

    with patch("api.chat_processing.orchestrator.handle", mock_handle), patch(
        "api.chat_processing.execute_action", mock_exec
    ), patch(
        "api.chat_processing._build_enriched_context",
        AsyncMock(return_value={}),
    ):
        result = await _process_message_internal(
            "Ouvre OBS s'il te plaît",
            conv_id,
            voice_mode=True,
        )

    assert result["action"] == {"type": "open_app", "name": "OBS"}
    mock_exec.assert_awaited_once()
    assert mock_exec.await_args.args[0]["type"] == "open_app"
    assert "```action" not in result["text"]

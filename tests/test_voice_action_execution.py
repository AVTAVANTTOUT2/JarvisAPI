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
    """Un « oui » vocal consomme le plan en attente et l'exécute une seule fois.

    Cette propriété était vérifiée sur ``_maybe_execute_pending_voice_action``,
    un raccourci propre à la pile vocale. L'unification du moteur l'a retiré du
    chemin d'exécution : la confirmation est désormais consommée par le moteur
    canonique, avant toute construction de contexte. Le test vise donc le
    chemin réellement emprunté — viser l'ancien laissait une fonction morte
    sous couverture, la forme la plus trompeuse de dette, puisque la propriété
    semblait tenue alors que plus personne n'exécutait ce code.

    Le moteur unifié fait une passe de reformulation pour les types listés dans
    ``ACTIONS_WITH_FOLLOWUP``, dont ``terminal``. Elle est doublée ici : la suite
    standard est hors ligne et ne doit jamais transformer une connexion refusée
    puis avalée par le chemin de repli en faux test vert. Reste ce qui compte
    réellement : le plan serveur est consommé et exécuté exactement une fois,
    avec ``confirmed``.
    """
    import api.chat_actions as chat_actions
    from api.action_confirmations import reset_pending_proposals_for_tests
    from api.chat_processing import _process_message_internal

    reset_pending_proposals_for_tests()
    chat_actions._maybe_store_pending_proposal(
        {"type": "terminal", "shell_plan_id": "server-plan"},
        conversation_id=7,
        confirmation_session_id="voice:test",
    )
    execute = AsyncMock(return_value={"ok": True, "output": "done"})
    followup = AsyncMock(return_value={
        "response": "Action exécutée.",
        "emotion": "neutral",
        "agent": "orchestrator",
        "model": "test-double",
        "tokens_in": 0,
        "tokens_out": 0,
        "cost": 0.0,
    })

    with (
        patch("api.chat_processing.execute_action", execute),
        patch("api.chat_processing.orchestrator.handle", followup),
        patch("api.chat_processing.save_message"),
        patch("api.chat_processing.update_conversation_activity"),
    ):
        result = await _process_message_internal(
            "oui",
            7,
            voice_mode=True,
            confirmation_session_id="voice:test",
        )
        # Le plan est à usage unique : un second « oui » ne doit rien réexécuter.
        again = await _process_message_internal(
            "oui",
            7,
            voice_mode=True,
            confirmation_session_id="voice:test",
        )

    execute.assert_awaited_once_with({
        "type": "terminal",
        "shell_plan_id": "server-plan",
        "confirmed": True,
    })
    assert result["action_result"] == {"ok": True, "output": "done"}
    assert execute.await_count == 1, "un plan confirmé ne se rejoue pas"
    assert again["action_result"] != {"ok": True, "output": "done"}


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


@pytest.mark.asyncio
async def test_voice_json_example_outside_action_fence_is_never_executed():
    from api.voice_processing import _process_voice_fast

    raw = 'Exemple seulement : {"type":"open_app","name":"Calculator"}'
    execute = AsyncMock(return_value={"ok": True})
    canonical = AsyncMock(return_value={
        "response": raw,
        "agent": "info",
        "model": "deepseek-v4-flash",
        "tokens_in": 1,
        "tokens_out": 1,
        "cost": 0.0,
        "emotion": "neutral",
    })
    with patch(
        "api.voice_cognitive.maybe_handle_cognitive_voice",
        AsyncMock(return_value=None),
    ), patch(
        "api.chat_processing.orchestrator.handle", canonical,
    ), patch(
        "api.chat_processing.execute_action", execute,
    ), patch(
        "api.chat_processing._build_enriched_context", AsyncMock(return_value={}),
    ), patch(
        "api.voice_processing._persist_voice_messages_async",
    ), patch(
        "api.voice_processing._save_voice_debug_trace", return_value=1,
    ), patch(
        "api.voice_processing._broadcast_voice_debug", AsyncMock(),
    ):
        result = await _process_voice_fast(raw, 77)

    assert result["action"] is None
    assert result["text"] == raw
    canonical.assert_awaited_once()
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_voice_lance_confirms_pending_shell_not_cursor_job():
    """« lance » vocal doit confirmer le plan shell en attente, pas un job Cursor."""
    from unittest.mock import AsyncMock, patch

    from api.action_confirmations import reset_pending_proposals_for_tests, store_pending_proposal
    from api.voice_cognitive import maybe_handle_cognitive_voice

    reset_pending_proposals_for_tests()
    store_pending_proposal(
        {"type": "terminal", "shell_plan_id": "server-plan"},
        conversation_id=9,
        session_id="local-voice:9",
    )

    mock_confirm = AsyncMock(return_value={"job_id": "cursor-1", "status": "running"})

    with (
        patch("integrations.cursor_delegation.cursor_delegation") as cd,
        patch(
            "database.cursor_jobs.list_jobs_by_statuses",
            return_value=[
                {
                    "job_id": "cursor-1",
                    "interaction_mode": "voice",
                    "status": "awaiting_confirmation",
                }
            ],
        ),
    ):
        cd.confirm = mock_confirm
        result = await maybe_handle_cognitive_voice(
            "lance",
            9,
            t0=0.0,
            confirmation_session_id="local-voice:9",
        )

    assert result is None, "le préambule cognitif doit céder au plan shell en attente"
    mock_confirm.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", ["confirme", "démarre", "demarre", "ok lance"])
async def test_voice_cursor_phrases_confirm_pending_shell_plan(phrase: str):
    """Les phrases Cursor (« confirme », « démarre »…) doivent confirmer un plan shell en attente."""
    import api.chat_actions as chat_actions
    from api.action_confirmations import reset_pending_proposals_for_tests
    from api.chat_processing import _process_message_internal

    reset_pending_proposals_for_tests()
    chat_actions._maybe_store_pending_proposal(
        {"type": "terminal", "shell_plan_id": "server-plan"},
        conversation_id=9,
        confirmation_session_id="local-voice:9",
    )
    execute = AsyncMock(return_value={"ok": True, "output": "done"})
    followup = AsyncMock(
        return_value={
            "emotion": "neutral",
            "response": "Commande exécutée.",
            "agent": "orchestrator",
            "model": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "cost": 0.0,
        }
    )

    with (
        patch("api.chat_processing.execute_action", execute),
        patch("api.chat_processing.orchestrator.handle", followup),
        patch("api.chat_processing.save_message"),
        patch("api.chat_processing.update_conversation_activity"),
    ):
        result = await _process_message_internal(
            phrase,
            9,
            voice_mode=True,
            confirmation_session_id="local-voice:9",
        )

    execute.assert_awaited_once_with({
        "type": "terminal",
        "shell_plan_id": "server-plan",
        "confirmed": True,
    })
    followup.assert_awaited_once()
    assert result["action_result"] == {"ok": True, "output": "done"}

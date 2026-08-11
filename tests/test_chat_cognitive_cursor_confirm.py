"""Confirmations Cursor en chat — « lance » doit démarrer un job en attente."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api.chat_cognitive import (
    resolve_pending_cursor_job_for_confirmation,
    should_run_cursor_cognitive_path,
)
from jarvis.cognitive.models import TaskIntent


def _answer_intent() -> TaskIntent:
    return TaskIntent(
        interaction_mode="chat",
        domain="general",
        complexity="standard",
        execution_type="answer",
        reasoning_model="deepseek-v4-flash",
        reason="test",
    )


def _cursor_intent() -> TaskIntent:
    return TaskIntent(
        interaction_mode="chat",
        domain="dev",
        complexity="standard",
        execution_type="cursor",
        reasoning_model="deepseek-v4-flash",
        reason="test",
        template_id="bug_fix",
    )


def test_should_run_cursor_path_for_new_technical_task() -> None:
    assert should_run_cursor_cognitive_path(
        "corrige le bug dans api/foo.py",
        _cursor_intent(),
        conversation_id=1,
        confirmation_session_id="sess-1",
    )


def test_should_run_cursor_path_for_lance_confirmation() -> None:
    assert should_run_cursor_cognitive_path(
        "lance",
        _answer_intent(),
        conversation_id=1,
        confirmation_session_id="sess-1",
    )


def test_should_not_run_cursor_path_for_lance_with_shell_pending() -> None:
    with patch(
        "api.action_confirmations.peek_pending_proposal",
        return_value={"type": "terminal"},
    ):
        assert not should_run_cursor_cognitive_path(
            "lance",
            _answer_intent(),
            conversation_id=1,
            confirmation_session_id="sess-1",
        )


@pytest.mark.asyncio
async def test_chat_internal_confirms_pending_cursor_on_lance() -> None:
    from api.chat_processing import _process_message_internal

    confirmed_job = {"job_id": "job-42", "status": "running"}

    with (
        patch(
            "api.chat_cognitive.route_request",
            return_value=_answer_intent(),
        ),
        patch(
            "integrations.cursor_delegation.cursor_delegation.confirm",
            new_callable=AsyncMock,
            return_value=confirmed_job,
        ),
        patch(
            "database.cursor_jobs.list_jobs_by_statuses",
            return_value=[
                {
                    "job_id": "job-42",
                    "status": "awaiting_confirmation",
                    "interaction_mode": "chat",
                    "routing": {"conversation_id": 7},
                }
            ],
        ),
        patch("api.chat_processing.save_message"),
        patch("api.chat_processing.update_conversation_activity"),
        patch("api.chat_processing._build_enriched_context", AsyncMock(return_value={})),
        patch(
            "api.chat_processing.orchestrator.handle",
            AsyncMock(side_effect=AssertionError("orchestrator ne doit pas être appelé")),
        ),
    ):
        result = await _process_message_internal(
            "lance",
            conversation_id=7,
            confirmation_session_id="sess-chat",
        )

    assert result["agent"] == "cognitive"
    assert result["action_result"]["job_id"] == "job-42"
    assert "parti" in result["text"].lower()


def test_resolve_pending_cursor_job_scopes_by_conversation_and_mode() -> None:
    jobs = [
        {
            "job_id": "voice-other",
            "status": "awaiting_confirmation",
            "interaction_mode": "voice",
            "routing": {"conversation_id": 2},
        },
        {
            "job_id": "chat-old",
            "status": "awaiting_confirmation",
            "interaction_mode": "chat",
            "routing": {"conversation_id": 1},
        },
        {
            "job_id": "chat-new",
            "status": "awaiting_confirmation",
            "interaction_mode": "chat",
            "routing": {"conversation_id": 1},
        },
    ]
    with patch("database.cursor_jobs.list_jobs_by_statuses", return_value=jobs):
        assert resolve_pending_cursor_job_for_confirmation(1, "chat")["job_id"] == "chat-new"
        assert resolve_pending_cursor_job_for_confirmation(2, "voice")["job_id"] == "voice-other"
        assert resolve_pending_cursor_job_for_confirmation(1, "voice") is None


@pytest.mark.asyncio
async def test_lance_in_chat_does_not_confirm_voice_pending_job() -> None:
    from api.chat_cognitive import maybe_confirm_pending_cursor

    with (
        patch(
            "database.cursor_jobs.list_jobs_by_statuses",
            return_value=[
                {
                    "job_id": "job-voice",
                    "status": "awaiting_confirmation",
                    "interaction_mode": "voice",
                    "routing": {"conversation_id": 9},
                }
            ],
        ),
        patch(
            "integrations.cursor_delegation.cursor_delegation.confirm",
            new_callable=AsyncMock,
        ) as confirm_mock,
    ):
        result = await maybe_confirm_pending_cursor(
            "lance",
            conversation_id=9,
            interaction_mode="chat",
        )

    assert result is None
    confirm_mock.assert_not_called()

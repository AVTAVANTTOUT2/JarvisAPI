from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agents.autonomous_loop import run_autonomous_loop
from agents.devagent.agentic_runtime import (
    AgenticRuntimeUnavailable,
    RuntimeDelegationResult,
)


def _technical_intent():
    return SimpleNamespace(
        execution_type="cursor",
        template_id="bug_fix",
        to_diagnostic=lambda: {"execution_type": "cursor", "risk": "medium"},
    )


@pytest.mark.asyncio
async def test_technical_loop_uses_generic_engineering_facade():
    events: list[tuple[str, dict]] = []

    async def on_event(event_type, payload):
        events.append((event_type, payload))

    delegated = {
        "job_id": "job-1",
        "run_id": "run-1",
        "status": "queued",
        "worktree_path": "/tmp/worktree",
        "branch_name": "jarvis/agentic/job-1",
        "legacy": False,
    }
    with patch("jarvis.cognitive.route_request", return_value=_technical_intent()), patch(
        "agents.autonomous_loop.delegate_engineering_task",
        new=AsyncMock(return_value=delegated),
    ) as delegate, patch("agents.autonomous_loop.llm.chat", new=AsyncMock()) as chat:
        result = await run_autonomous_loop(
            "Corrige le bug Python et ajoute un test",
            None,
            {},
            on_event=on_event,
        )

    assert result["final_status"] == "running"
    assert result["results"][0]["result"]["delegation"]["run_id"] == "run-1"
    kwargs = delegate.await_args.kwargs
    assert kwargs["delivery_mode"] == "pr_only"
    assert kwargs["auto_start"] is True
    assert kwargs["idempotency_key"].startswith("loop:none:")
    assert events[-1][1]["agentic_run_id"] == "run-1"
    chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_technical_loop_fails_closed_without_runtime_or_legacy_fallback():
    with patch("jarvis.cognitive.route_request", return_value=_technical_intent()), patch(
        "agents.autonomous_loop.delegate_engineering_task",
        new=AsyncMock(side_effect=AgenticRuntimeUnavailable("aucun runtime")),
    ), patch("agents.autonomous_loop.llm.chat", new=AsyncMock()) as chat:
        result = await run_autonomous_loop(
            "Implémente cette fonctionnalité",
            None,
            {},
        )

    assert result["final_status"] == "failed"
    assert "indisponible" in result["synthesis"]
    chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_nontechnical_loop_also_uses_generic_runtime():
    intent = SimpleNamespace(
        execution_type="flash",
        to_diagnostic=lambda: {"execution_type": "flash", "risk": "low"},
    )
    runtime_result = RuntimeDelegationResult(
        run_id="run-workflow",
        status="queued",
        phase="queued",
    )
    with patch("jarvis.cognitive.route_request", return_value=intent), patch(
        "agents.autonomous_loop.delegate_agentic_task",
        new=AsyncMock(return_value=runtime_result),
    ) as delegate, patch("agents.autonomous_loop.llm.chat", new=AsyncMock()) as chat:
        result = await run_autonomous_loop("Ajoute une tâche demain", None, {})

    assert result["final_status"] == "running"
    assert result["results"][0]["result"]["delegation"]["run_id"] == "run-workflow"
    assert delegate.await_args.kwargs["permissions"] == ("tasks:read", "tasks:write")
    chat.assert_not_awaited()

"""Contrats provider-neutral des workflows techniques planifiés."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_self_improvement_delegates_once_per_window(monkeypatch, tmp_path):
    import config
    from agents.devagent import agentic_runtime
    from scripts import self_improvement

    evidence = {
        "type": "action_failures",
        "action_type": "task_create",
        "count": 8,
        "impact": "échecs répétés",
        "risk": "medium",
        "template_id": "bug_fix",
    }
    delegate = AsyncMock(
        return_value={"job_id": "engineering-job", "run_id": "agentic-run"}
    )
    monkeypatch.setattr(config, "SELF_IMPROVEMENT_ENABLED", True)
    monkeypatch.setattr(self_improvement, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(self_improvement, "collect_evidence", lambda: [evidence])
    monkeypatch.setattr(self_improvement, "_delegation_window", lambda: "2026-W33")
    monkeypatch.setattr(agentic_runtime, "delegate_engineering_task", delegate)

    first = await self_improvement.propose_improvements(auto_delegate=True)
    second = await self_improvement.propose_improvements(auto_delegate=True)

    assert first["jobs"][0]["run_id"] == "agentic-run"
    assert second["jobs"] == []
    assert len(self_improvement.list_proposals()) == 1
    delegate.assert_awaited_once()
    kwargs = delegate.await_args.kwargs
    assert kwargs["origin"] == "scheduler"
    assert kwargs["channel"] == "self_improvement"
    assert kwargs["task_id"] == first["proposals"][0]["id"]
    assert kwargs["idempotency_key"].startswith("scheduler:self-improvement:")
    assert kwargs["permissions"] == ("workspace:read", "workspace:write")
    assert kwargs["delivery_mode"] == "pr_only"


def test_self_improvement_counts_generic_failures(monkeypatch):
    from jarvis.agentic.models import AgenticRunStatus
    from jarvis.agentic import service
    from scripts import self_improvement

    fake_service = SimpleNamespace(
        list=lambda **_kwargs: [
            SimpleNamespace(status=AgenticRunStatus.FAILED),
            SimpleNamespace(status=AgenticRunStatus.EXPIRED),
            SimpleNamespace(status=AgenticRunStatus.PROVIDER_UNAVAILABLE),
        ]
    )
    monkeypatch.setattr(service, "get_agentic_service", lambda: fake_service)

    evidence = self_improvement.collect_evidence()

    failures = [item for item in evidence if item["type"] == "agentic_failures"]
    assert failures == [
        {
            "type": "agentic_failures",
            "count": 3,
            "impact": "Échecs agentiques répétés — revue du contexte et des tests",
            "risk": "medium",
            "template_id": "regression_review",
        }
    ]


@pytest.mark.asyncio
async def test_scheduled_test_generation_has_stable_idempotency(monkeypatch):
    import config
    from agents.devagent import agentic_runtime
    from scripts.quality_delegation import delegate_missing_tests

    delegate = AsyncMock(
        side_effect=[
            {"job_id": "same-job", "run_id": "same-run"},
            {"job_id": "same-job", "run_id": "same-run"},
        ]
    )
    monkeypatch.setattr(config, "AUTO_TEST_GEN_ENABLED", True)
    monkeypatch.setattr(config, "AUTO_TEST_GEN_TARGET_DIRS", "api,jarvis/cognitive")
    monkeypatch.setattr(agentic_runtime, "delegate_engineering_task", delegate)

    await delegate_missing_tests(
        interaction_mode="scheduled",
        auto_start=True,
        require_confirmation=False,
    )
    await delegate_missing_tests(
        interaction_mode="scheduled",
        auto_start=True,
        require_confirmation=False,
    )

    first, second = delegate.await_args_list
    assert first.kwargs["idempotency_key"] == second.kwargs["idempotency_key"]
    assert first.kwargs["task_id"] == second.kwargs["task_id"]


def test_scheduler_prevents_overlapping_instances():
    from scripts.scheduler import scheduler

    assert scheduler._job_defaults["coalesce"] is True
    assert scheduler._job_defaults["max_instances"] == 1


def test_scheduled_agentic_paths_have_no_static_provider_dependency():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "scripts/self_improvement.py",
        "scripts/self_healing.py",
        "scripts/quality_delegation.py",
        "scripts/scheduler.py",
    ):
        source = (root / relative).read_text(encoding="utf-8").casefold()
        assert "cursor" not in source
        assert "integrations.opencode" not in source

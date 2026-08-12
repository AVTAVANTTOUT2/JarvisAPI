"""Contrats du self-healing report-only / runtime agentique PR-only."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


@pytest.fixture
def diagnosis() -> dict:
    return {
        "root_cause": "Division par zéro dans buggy.py",
        "confidence": "high",
        "file": "buggy.py",
    }


@pytest.mark.asyncio
async def test_disabled_by_default(tmp_db, monkeypatch):
    from scripts.self_healing import handle_crash_loop

    monkeypatch.setattr("config.SELF_HEALING_ENABLED", False)

    result = await handle_crash_loop("Traceback: boom")

    assert result == {"ok": False, "reason": "SELF_HEALING_ENABLED désactivé"}


@pytest.mark.asyncio
async def test_diagnoses_without_modifying_code_when_self_repair_is_disabled(
    tmp_db,
    diagnosis,
    monkeypatch,
):
    from database import get_unread_notifications
    from scripts.self_healing import handle_crash_loop

    monkeypatch.setattr("config.SELF_HEALING_ENABLED", True)
    monkeypatch.setattr("config.SELF_REPAIR_ENABLED", False)

    with patch(
        "scripts.self_healing.diagnose_crash",
        new=AsyncMock(return_value=diagnosis),
    ):
        result = await handle_crash_loop("Traceback: ZeroDivisionError")

    assert result["action"] == "diagnosed_only"
    assert result["reason"] == "SELF_REPAIR_ENABLED=false"
    notifications = [
        item
        for item in get_unread_notifications(10)
        if "Diagnostic self-healing" in item["title"]
    ]
    assert len(notifications) == 1
    assert "Division par zéro" in notifications[0]["content"]


@pytest.mark.asyncio
async def test_diagnoses_only_when_runtime_is_unavailable(tmp_db, diagnosis, monkeypatch):
    from agents.devagent import agentic_runtime
    from scripts.self_healing import handle_crash_loop

    monkeypatch.setattr("config.SELF_HEALING_ENABLED", True)
    monkeypatch.setattr("config.SELF_REPAIR_ENABLED", True)
    monkeypatch.setattr(
        agentic_runtime,
        "delegate_engineering_task",
        AsyncMock(side_effect=agentic_runtime.AgenticRuntimeUnavailable("indisponible")),
    )

    with patch(
        "scripts.self_healing.diagnose_crash",
        new=AsyncMock(return_value=diagnosis),
    ):
        result = await handle_crash_loop("Traceback: ZeroDivisionError")

    assert result["action"] == "diagnosed_only"
    assert result["reason"] == "indisponible"


@pytest.mark.asyncio
async def test_self_repair_is_delegated_pr_only(tmp_db, diagnosis, monkeypatch):
    from agents.devagent import agentic_runtime
    from scripts.self_healing import handle_crash_loop

    delegate = AsyncMock(
        return_value={"job_id": "job-self-repair", "run_id": "run-self-repair"}
    )
    monkeypatch.setattr("config.SELF_HEALING_ENABLED", True)
    monkeypatch.setattr("config.SELF_REPAIR_ENABLED", True)
    monkeypatch.setattr(agentic_runtime, "delegate_engineering_task", delegate)

    with patch(
        "scripts.self_healing.diagnose_crash",
        new=AsyncMock(return_value=diagnosis),
    ):
        result = await handle_crash_loop("Traceback: ZeroDivisionError")

    assert result["action"] == "agentic_delegated"
    assert result["job_id"] == "job-self-repair"
    assert result["run_id"] == "run-self-repair"
    kwargs = delegate.await_args.kwargs
    assert kwargs["delivery_mode"] == "pr_only"
    assert kwargs["interaction_mode"] == "scheduled"
    assert kwargs["origin"] == "supervisor"
    assert kwargs["channel"] == "self_healing"
    assert kwargs["permissions"] == ("workspace:read", "workspace:write")
    assert kwargs["idempotency_key"].startswith("self-healing:")
    assert kwargs["auto_start"] is True
    assert kwargs["require_confirmation"] is False


@pytest.mark.asyncio
async def test_runtime_failure_never_falls_back_to_direct_patch(
    tmp_db,
    diagnosis,
    monkeypatch,
):
    from agents.devagent import agentic_runtime
    from scripts.self_healing import handle_crash_loop

    monkeypatch.setattr("config.SELF_HEALING_ENABLED", True)
    monkeypatch.setattr("config.SELF_REPAIR_ENABLED", True)
    monkeypatch.setattr(
        agentic_runtime,
        "delegate_engineering_task",
        AsyncMock(side_effect=RuntimeError("Runtime indisponible")),
    )

    with patch(
        "scripts.self_healing.diagnose_crash",
        new=AsyncMock(return_value=diagnosis),
    ):
        result = await handle_crash_loop("Traceback: ZeroDivisionError")

    assert result["ok"] is False
    assert result["action"] == "runtime_failed_pr_only"
    assert "Runtime indisponible" in result["error"]


def test_self_healing_contains_no_checkout_mutation_primitive():
    source = (
        Path(__file__).resolve().parents[1] / "scripts" / "self_healing.py"
    ).read_text(encoding="utf-8")

    for forbidden in ("subprocess", "write_text(", "git commit", "git revert"):
        assert forbidden not in source


def test_self_healing_status_exposes_pr_only_contract(monkeypatch):
    from api.misc_insights import api_self_healing_status

    monkeypatch.setattr("config.SELF_HEALING_ENABLED", True)
    monkeypatch.setattr("config.SELF_REPAIR_ENABLED", False)

    result = asyncio.run(api_self_healing_status())

    assert result == {
        "enabled": True,
        "self_repair_enabled": False,
        "delivery_mode": "pr_only",
    }


@pytest.mark.asyncio
async def test_diagnose_crash_parses_llm_response():
    from scripts.self_healing import diagnose_crash

    fake_response = {
        "content": (
            '{"root_cause": "fuite mémoire", "confidence": "medium", '
            '"file": null}'
        )
    }
    with patch("llm.chat", new=AsyncMock(return_value=fake_response)):
        result = await diagnose_crash("Traceback: MemoryError")

    assert result["root_cause"] == "fuite mémoire"


@pytest.mark.asyncio
async def test_diagnose_crash_never_raises_on_llm_failure():
    from scripts.self_healing import diagnose_crash

    with patch("llm.chat", new=AsyncMock(side_effect=RuntimeError("DeepSeek down"))):
        result = await diagnose_crash("Traceback: x")

    assert result["confidence"] == "low"
    assert result["file"] is None

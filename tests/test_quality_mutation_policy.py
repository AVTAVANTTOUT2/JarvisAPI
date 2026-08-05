"""Contrats PR-only des opérations qualité qui modifient le dépôt."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _request(*, client: str, host: str = "localhost:8000") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/quality/ci/install-hook",
            "headers": [(b"host", host.encode())],
            "client": (client, 54321),
        }
    )


@pytest.mark.asyncio
async def test_security_fix_is_delegated_with_confirmation_and_pr_only(monkeypatch):
    import config
    from integrations.cursor_delegation import cursor_delegation
    from scripts.quality_delegation import delegate_security_fix

    enqueue = AsyncMock(return_value={"job_id": "job-safe"})
    monkeypatch.setattr(config, "SECURITY_AUTO_FIX_ENABLED", True)
    monkeypatch.setattr(cursor_delegation, "enqueue", enqueue)

    job = await delegate_security_fix(
        {
            "rule": "secret_github_token",
            "file": "integrations/example.py",
            "line": 42,
        },
        interaction_mode="chat",
        auto_start=False,
        require_confirmation=True,
    )

    assert job["job_id"] == "job-safe"
    kwargs = enqueue.await_args.kwargs
    assert kwargs["delivery_mode"] == "pr_only"
    assert kwargs["require_confirmation"] is True
    assert kwargs["auto_start"] is False
    assert "ghp_" not in kwargs["user_request"]


@pytest.mark.asyncio
async def test_dangerous_pattern_cannot_use_mechanical_fix(monkeypatch):
    import config
    from scripts.quality_delegation import QualityDelegationError, delegate_security_fix

    monkeypatch.setattr(config, "SECURITY_AUTO_FIX_ENABLED", True)
    with pytest.raises(QualityDelegationError, match="patterns dangereux"):
        await delegate_security_fix(
            {"rule": "eval_usage", "file": "bad.py", "line": 1},
            interaction_mode="chat",
            auto_start=False,
            require_confirmation=True,
        )


@pytest.mark.asyncio
async def test_missing_tests_scheduler_delegates_only_to_pr_worktree(monkeypatch):
    import config
    from integrations.cursor_delegation import cursor_delegation
    from scripts.quality_delegation import delegate_missing_tests

    enqueue = AsyncMock(return_value={"job_id": "job-tests"})
    monkeypatch.setattr(config, "AUTO_TEST_GEN_ENABLED", True)
    monkeypatch.setattr(config, "AUTO_TEST_GEN_TARGET_DIRS", "api,jarvis/cognitive")
    monkeypatch.setattr(cursor_delegation, "enqueue", enqueue)

    job = await delegate_missing_tests(
        interaction_mode="scheduled",
        auto_start=True,
        require_confirmation=False,
    )

    assert job["job_id"] == "job-tests"
    kwargs = enqueue.await_args.kwargs
    assert kwargs["delivery_mode"] == "pr_only"
    assert kwargs["interaction_mode"] == "scheduled"
    assert kwargs["auto_start"] is True
    assert kwargs["require_confirmation"] is False


@pytest.mark.asyncio
async def test_missing_tests_rejects_path_traversal_before_enqueue(monkeypatch):
    import config
    from scripts.quality_delegation import QualityDelegationError, delegate_missing_tests

    monkeypatch.setattr(config, "AUTO_TEST_GEN_ENABLED", True)
    monkeypatch.setattr(config, "AUTO_TEST_GEN_TARGET_DIRS", "api,../outside")

    with pytest.raises(QualityDelegationError, match="cible interdite"):
        await delegate_missing_tests(
            interaction_mode="chat",
            auto_start=False,
            require_confirmation=True,
        )


@pytest.mark.asyncio
async def test_scheduler_test_generation_uses_quality_delegation(monkeypatch):
    import config
    from scripts import quality_delegation, scheduler

    delegate = AsyncMock(return_value={"job_id": "job-scheduled"})
    monkeypatch.setattr(config, "AUTO_TEST_GEN_ENABLED", True)
    monkeypatch.setattr(quality_delegation, "delegate_missing_tests", delegate)

    result = await scheduler._test_gen_job.__wrapped__()

    assert result["status"] == "ok"
    assert "job-scheduled" in result["output"]
    delegate.assert_awaited_once_with(
        interaction_mode="scheduled",
        auto_start=True,
        require_confirmation=False,
    )


@pytest.mark.asyncio
async def test_quality_routes_return_cursor_proposals(monkeypatch):
    import database
    from api import router_quality
    from scripts import quality_delegation

    security_job = {
        "job_id": "job-security",
        "status": "awaiting_confirmation",
        "title": "Security fix",
    }
    tests_job = {
        "job_id": "job-tests",
        "status": "awaiting_confirmation",
        "title": "Missing tests",
    }
    security_delegate = AsyncMock(return_value=security_job)
    tests_delegate = AsyncMock(return_value=tests_job)
    monkeypatch.setattr(
        database,
        "get_security_findings",
        lambda *_args, **_kwargs: [
            {"id": 7, "rule": "secret_github_token", "file": "a.py", "line": 1}
        ],
    )
    monkeypatch.setattr(quality_delegation, "delegate_security_fix", security_delegate)
    monkeypatch.setattr(quality_delegation, "delegate_missing_tests", tests_delegate)

    security = await router_quality.api_quality_security_fix(7)
    generated = await router_quality.api_quality_generate_tests()

    assert security["delegated"] is True
    assert security["job"]["job_id"] == "job-security"
    assert generated["delegated"] is True
    assert generated["job"]["job_id"] == "job-tests"
    security_delegate.assert_awaited_once()
    tests_delegate.assert_awaited_once_with(
        interaction_mode="chat",
        auto_start=False,
        require_confirmation=True,
    )


@pytest.mark.asyncio
async def test_hook_api_is_local_and_never_forces_overwrite(monkeypatch):
    import database
    from api import router_quality
    from scripts import install_git_hooks

    install = MagicMock(return_value={"ok": True, "path": "/safe/pre-commit"})
    audit = MagicMock(return_value=1)
    monkeypatch.setattr(install_git_hooks, "install", install)
    monkeypatch.setattr(database, "log_llm_action", audit)

    with pytest.raises(HTTPException) as remote:
        await router_quality.api_quality_ci_install_hook(
            _request(client="203.0.113.20")
        )
    assert remote.value.status_code == 403

    with pytest.raises(HTTPException) as forced:
        await router_quality.api_quality_ci_install_hook(
            _request(client="127.0.0.1"),
            force=True,
        )
    assert forced.value.status_code == 409

    result = await router_quality.api_quality_ci_install_hook(
        _request(client="127.0.0.1")
    )
    assert result["ok"] is True
    install.assert_called_once_with(force=False)
    audit.assert_called_once()


@pytest.mark.asyncio
async def test_hook_api_fails_before_mutation_when_audit_is_down(monkeypatch):
    import database
    from api import router_quality
    from scripts import install_git_hooks

    install = AsyncMock()

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(install_git_hooks, "install", install)
    monkeypatch.setattr(database, "log_llm_action", fail_audit)

    with pytest.raises(HTTPException) as exc:
        await router_quality.api_quality_ci_install_hook(
            _request(client="127.0.0.1")
        )
    assert exc.value.status_code == 503
    install.assert_not_called()

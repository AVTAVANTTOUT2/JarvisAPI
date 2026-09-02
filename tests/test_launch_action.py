"""Action ``launch`` / ``open_app`` — URL YouTube sans confirmation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from actions import execute_action
from integrations.computer import _OPEN, computer as computer_singleton


@pytest.fixture
def allow_computer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(computer_singleton, "allowed", True)
    monkeypatch.setattr(computer_singleton, "home", str(tmp_path))
    captured: list[tuple[str, ...]] = []

    async def fake_run(argv, **kwargs):
        captured.append(tuple(argv))
        return {
            "ok": True,
            "argv": list(argv),
            "stdout": "",
            "stderr": "",
            "returncode": 0,
        }

    monkeypatch.setattr(computer_singleton, "_run_argv", fake_run)
    return captured


@pytest.mark.asyncio
async def test_launch_opens_squeezie_without_confirmation(allow_computer):
    result = await execute_action(
        {"type": "launch", "url": "https://www.youtube.com/@Squeezie"}
    )
    assert result["ok"] is True
    assert result.get("needs_confirmation") is not True
    assert allow_computer == [
        (_OPEN, "https://www.youtube.com/@Squeezie"),
    ]
    assert "Squeezie" in result["message"]


@pytest.mark.asyncio
async def test_launch_youtube_query_expands_handle(allow_computer):
    result = await execute_action(
        {"type": "launch", "query": "Squeezie", "name": "YouTube"}
    )
    assert result["ok"] is True
    assert allow_computer == [
        (_OPEN, "-a", "YouTube", "https://www.youtube.com/@Squeezie"),
    ]


@pytest.mark.asyncio
async def test_open_app_alias_still_opens_named_app(allow_computer):
    result = await execute_action({"type": "open_app", "name": "Safari"})
    assert result["ok"] is True
    assert allow_computer == [(_OPEN, "-a", "Safari")]


@pytest.mark.asyncio
async def test_launch_rejects_javascript_url(allow_computer):
    result = await execute_action({"type": "launch", "url": "javascript:alert(1)"})
    assert result["ok"] is False
    assert allow_computer == []


@pytest.mark.asyncio
async def test_launch_rejects_shortcuts_url_bypass(allow_computer):
    result = await execute_action(
        {
            "type": "launch",
            "url": "shortcuts://run-shortcut?name=UnregisteredShortcut",
        }
    )
    assert result["ok"] is False
    assert allow_computer == []
    assert "raccourcis" in result["message"]


@pytest.mark.asyncio
async def test_launch_rejects_percent_encoded_file_traversal(
    allow_computer, tmp_path: Path
):
    result = await execute_action(
        {
            "type": "launch",
            "url": f"file://{tmp_path}/%2e%2e/%2e%2e/etc/passwd",
        }
    )
    assert result["ok"] is False
    assert allow_computer == []


@pytest.mark.asyncio
async def test_launch_rejects_percent_encoded_path_traversal(
    allow_computer, tmp_path: Path
):
    result = await execute_action(
        {
            "type": "launch",
            "path": f"{tmp_path}/%2e%2e/%2e%2e/etc/passwd",
        }
    )
    assert result["ok"] is False
    assert allow_computer == []


@pytest.mark.asyncio
async def test_launch_is_inert_without_computer_access(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(computer_singleton, "allowed", False)
    computer_singleton._run_argv = AsyncMock()
    result = await execute_action(
        {"type": "launch", "url": "https://www.youtube.com/@Squeezie"}
    )
    assert result["ok"] is False
    computer_singleton._run_argv.assert_not_called()

"""Contrôle indépendant du Screen Watcher + couplage Ollama."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.screen_watcher import ScreenWatcher


@pytest.mark.asyncio
async def test_ensure_started_blocked_without_ollama() -> None:
    sw = ScreenWatcher()
    sw.enabled = True
    with patch(
        "integrations.ollama_control.check_ollama_health",
        return_value={"healthy": False, "status": "stopped", "error": "down"},
    ):
        result = await sw.ensure_started(require_ollama=True)
    assert result["ok"] is False
    assert result["status"] == "blocked_ollama"
    assert sw.status == "blocked_ollama"
    assert sw.running is False


@pytest.mark.asyncio
async def test_ensure_started_disabled_by_config() -> None:
    sw = ScreenWatcher()
    sw.enabled = False
    with patch.object(sw, "refresh_config_enabled"):
        result = await sw.ensure_started(require_ollama=False)
    assert result["ok"] is False
    assert result["status"] == "disabled"


@pytest.mark.asyncio
async def test_stop_independent_does_not_require_daemon() -> None:
    sw = ScreenWatcher()
    sw.enabled = True
    sw.running = True
    sw._status = "running"
    sw.last_heartbeat = 1.0

    async def _noop_loop() -> None:
        sw.running = True
        await asyncio.sleep(10)

    sw._loop_task = asyncio.create_task(_noop_loop())
    # Stop should end the loop without touching a daemon
    with patch.object(sw, "defer_for_voice"):
        result = await sw.stop_async(reason="manual")
    assert result["ok"] is True
    assert result["status"] == "stopped"
    assert sw.running is False


@pytest.mark.asyncio
async def test_double_start_is_idempotent() -> None:
    sw = ScreenWatcher()
    sw.enabled = True
    sw.running = True
    sw._status = "running"
    sw.last_heartbeat = 123.0
    sw._loop_task = MagicMock()
    sw._loop_task.done.return_value = False

    with patch(
        "integrations.ollama_control.check_ollama_health",
        return_value={
            "healthy": True,
            "vision_model_available": True,
            "vision_model_resolved": "qwen2.5-vl:7b",
        },
    ):
        result = await sw.ensure_started()
    assert result["ok"] is True
    assert "déjà" in result["message"].lower() or "deja" in result["message"].lower()


@pytest.mark.asyncio
async def test_stop_ollama_stops_screen_watcher_first() -> None:
    from api.service_control import _stop_service

    sw = MagicMock()
    sw.stop_async = AsyncMock(
        return_value={"ok": True, "status": "stopped", "service": "screen_watcher"}
    )

    with (
        patch("scripts.screen_watcher.screen_watcher", sw),
        patch(
            "integrations.ollama_control.stop_ollama",
            return_value={"ok": True, "status": "stopped", "healthy": False, "message": "Ollama arrêté"},
        ),
    ):
        result = await _stop_service("ollama")

    sw.stop_async.assert_awaited()
    assert result["ok"] is True
    assert "screen_watcher" in result


@pytest.mark.asyncio
async def test_start_ollama_does_not_start_screen_watcher() -> None:
    from api.service_control import _start_service

    sw = MagicMock()
    sw.ensure_started = AsyncMock()

    with (
        patch("scripts.screen_watcher.screen_watcher", sw),
        patch(
            "integrations.ollama_control.start_ollama",
            return_value={"ok": True, "healthy": True, "status": "running", "message": "Ollama démarré"},
        ),
    ):
        result = await _start_service("ollama")

    sw.ensure_started.assert_not_called()
    assert result["ok"] is True


def test_status_payload_not_derived_from_daemon_running() -> None:
    sw = ScreenWatcher()
    sw.enabled = True
    sw.running = False
    sw._status = "stopped"
    payload = sw.status_payload()
    assert payload["running"] is False
    assert payload["status"] == "stopped"


@pytest.mark.asyncio
async def test_manual_stop_does_not_flip_config_enabled() -> None:
    sw = ScreenWatcher()
    sw.enabled = True
    sw.running = False
    sw._status = "running"
    with patch.object(sw, "defer_for_voice"):
        await sw.stop_async(reason="manual")
    assert sw.enabled is True
    assert sw.status == "stopped"

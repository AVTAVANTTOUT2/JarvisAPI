"""Branchements supervisor du resource guard (sans tuer de process réel)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def test_run_resource_guard_tick_respects_interval(monkeypatch):
    import supervisor
    from jarvis.resource_guard import GuardReport

    fake = MagicMock()
    fake.should_tick.return_value = False
    fake.config = SimpleNamespace(dry_run=True)
    monkeypatch.setattr(supervisor, "_get_resource_guard", lambda: fake)
    monkeypatch.setattr(supervisor.config, "RESOURCE_GUARD_ENABLED", True)
    monkeypatch.setattr(supervisor.config, "RESOURCE_GUARD_INTERVAL_S", 30)

    assert supervisor._run_resource_guard_tick() is None
    fake.tick.assert_not_called()

    fake.should_tick.return_value = True
    fake.tick.return_value = GuardReport(
        level="ok",
        free_mb=4000.0,
        processes=[],
        actions=[],
        screen_watcher_running=True,
        ollama_idle_seconds=0.0,
    )
    out = supervisor._run_resource_guard_tick()
    assert out is not None
    assert out["level"] == "ok"
    fake.tick.assert_called_once()


def test_screen_watcher_unknown_assumes_running(monkeypatch):
    import supervisor

    monkeypatch.setattr(
        supervisor.httpx,
        "get",
        MagicMock(side_effect=ConnectionError("down")),
    )
    assert supervisor._screen_watcher_running_for_guard() is True


def test_screen_watcher_stopped_from_payload(monkeypatch):
    import supervisor

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"status": "stopped", "running": False}
    monkeypatch.setattr(supervisor.httpx, "get", MagicMock(return_value=resp))
    assert supervisor._screen_watcher_running_for_guard() is False

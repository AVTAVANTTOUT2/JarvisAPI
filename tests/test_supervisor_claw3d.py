"""Cycle de vie Claw3D piloté par le superviseur."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_invalid_claw3d_port_does_not_break_config_import():
    completed = subprocess.run(
        [sys.executable, "-c", "import config; print(config.CLAW3D_PORT)"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "CLAW3D_PORT": "invalide"},
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "3000"


@pytest.fixture
def supervisor_mod(monkeypatch, tmp_path):
    import supervisor as mod

    monkeypatch.setattr(mod.config, "CLAW3D_MANAGED_BY_SUPERVISOR", True)
    monkeypatch.setattr(mod.config, "CLAW3D_HOST", "127.0.0.1")
    monkeypatch.setattr(mod.config, "CLAW3D_PORT", 3000)
    monkeypatch.setattr(mod.config, "CLAW3D_MODE", "jarvis-readonly")
    monkeypatch.setattr(mod.config, "WEB_USE_HTTPS", True)
    monkeypatch.setattr(mod, "BACKEND_PORT", 8081)
    monkeypatch.setattr(mod, "LOGS_DIR", tmp_path / "logs")
    return mod


def test_start_claw3d_skips_when_not_installed(supervisor_mod, monkeypatch):
    monkeypatch.setattr(
        "scripts.claw3d.is_installed",
        lambda jarvis_root=None: False,
    )
    result = supervisor_mod._start_claw3d_sync()
    assert result["ok"] is True
    assert result["skipped"] is True
    assert result["code"] == "claw3d_not_installed"


def test_start_claw3d_skips_when_management_disabled(supervisor_mod, monkeypatch):
    monkeypatch.setattr(supervisor_mod.config, "CLAW3D_MANAGED_BY_SUPERVISOR", False)
    result = supervisor_mod._start_claw3d_sync()
    assert result["ok"] is True
    assert result["skipped"] is True


def test_start_claw3d_rejects_a_foreign_port_owner(supervisor_mod, monkeypatch):
    monkeypatch.setattr("scripts.claw3d.is_installed", lambda jarvis_root=None: True)
    monkeypatch.setattr("scripts.claw3d.is_running", lambda jarvis_root=None: False)
    monkeypatch.setattr(supervisor_mod, "_port_open", lambda port: True)
    monkeypatch.setattr(
        "scripts.claw3d.sync_managed_configuration",
        lambda *args, **kwargs: pytest.fail("une configuration ne doit pas être réécrite"),
    )

    result = supervisor_mod._start_claw3d_sync()

    assert result["ok"] is False
    assert result["code"] == "service_port_conflict"


def test_start_claw3d_configures_and_starts(supervisor_mod, monkeypatch):
    calls: list[tuple] = []

    monkeypatch.setattr("scripts.claw3d.is_installed", lambda jarvis_root=None: True)
    monkeypatch.setattr("scripts.claw3d.is_running", lambda jarvis_root=None: False)
    monkeypatch.setattr(supervisor_mod, "_port_open", lambda port: False)

    def sync(jarvis_root, *, mode, jarvis_origin, host, port):
        calls.append(("sync", mode, jarvis_origin, host, port))

    monkeypatch.setattr("scripts.claw3d.sync_managed_configuration", sync)
    monkeypatch.setattr("scripts.claw3d.running_pid", lambda jarvis_root=None: 4242)

    proc = MagicMock()
    proc.wait.return_value = 0
    proc.returncode = 0

    def fake_popen(*args, **kwargs):
        calls.append(("popen", args[0]))
        return proc

    monkeypatch.setattr(supervisor_mod.subprocess, "Popen", fake_popen)

    result = supervisor_mod._start_claw3d_sync()
    assert result["ok"] is True
    assert result["pid"] == 4242
    assert ("sync", "jarvis-readonly", "https://127.0.0.1:8081", "127.0.0.1", 3000) in calls
    popen_cmds = [c for c in calls if c[0] == "popen"]
    assert popen_cmds
    assert "scripts/claw3d.py" in popen_cmds[0][1]
    assert "start" in popen_cmds[0][1]


def test_stop_claw3d_idempotent_when_already_stopped(supervisor_mod, monkeypatch):
    monkeypatch.setattr("scripts.claw3d.is_installed", lambda jarvis_root=None: True)
    monkeypatch.setattr("scripts.claw3d.is_running", lambda jarvis_root=None: False)
    monkeypatch.setattr(supervisor_mod, "_port_open", lambda port: True)
    monkeypatch.setattr(
        supervisor_mod.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("un processus tiers ne doit pas être arrêté"),
    )
    monkeypatch.setattr(
        supervisor_mod,
        "_kill_port",
        lambda port: pytest.fail("un processus tiers ne doit pas être tué"),
    )
    result = supervisor_mod._stop_claw3d_sync()
    assert result["ok"] is True
    assert "déjà arrêté" in result["message"]


def test_stop_claw3d_propagates_a_safe_stop_failure(supervisor_mod, monkeypatch):
    monkeypatch.setattr("scripts.claw3d.is_installed", lambda jarvis_root=None: True)
    monkeypatch.setattr("scripts.claw3d.is_running", lambda jarvis_root=None: True)
    monkeypatch.setattr(
        supervisor_mod,
        "_kill_port",
        lambda port: pytest.fail("le repli par port est interdit"),
    )

    proc = MagicMock()
    proc.wait.return_value = 0
    proc.returncode = 7
    monkeypatch.setattr(supervisor_mod.subprocess, "Popen", lambda *args, **kwargs: proc)

    result = supervisor_mod._stop_claw3d_sync()

    assert result["ok"] is False
    assert result["code"] == "service_stop_failed"


@pytest.mark.asyncio
async def test_claw3d_status_exposes_a_port_conflict(supervisor_mod, monkeypatch):
    monkeypatch.setattr("scripts.claw3d.is_installed", lambda jarvis_root=None: True)
    monkeypatch.setattr("scripts.claw3d.is_running", lambda jarvis_root=None: False)
    monkeypatch.setattr(supervisor_mod, "_port_open", lambda port: True)
    service = next(svc for svc in supervisor_mod.SERVICES if svc["id"] == "claw3d")

    result = await supervisor_mod._svc_status(service)

    assert result["running"] is False
    assert result["port_conflict"] is True


def test_services_list_includes_claw3d(supervisor_mod):
    ids = {svc["id"] for svc in supervisor_mod.SERVICES}
    assert "claw3d" in ids

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import signal

import pytest

from integrations.opencode.config import OpenCodeSettings, RuntimeLayout
from integrations.opencode.lifecycle._files import atomic_write_json
from integrations.opencode.lifecycle.health import HealthReport
from integrations.opencode.lifecycle.install import VerificationReport
from integrations.opencode.lifecycle.process import (
    OpenCodeProcessManager,
    ProcessManagerError,
    ProcessOwnershipError,
    ProcessState,
)
from integrations.opencode.lifecycle.release import ReleaseManifest
from integrations.opencode.scripts import manager as manager_cli
from integrations.opencode.scripts.manager import build_parser


class _Installed:
    def __init__(self, binary: Path) -> None:
        self.binary = binary

    def verify(self, *, execute_binary: bool = True) -> VerificationReport:
        return VerificationReport(True, "1.18.16", "darwin-arm64", self.binary, ())


class _FakeProcess:
    pid = 424242
    returncode = None

    def poll(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0


def _layout(tmp_path: Path) -> RuntimeLayout:
    root = tmp_path / "plugin"
    root.mkdir()
    layout = RuntimeLayout.from_integration_root(root)
    layout.ensure()
    layout.binary_path.write_bytes(b"fake")
    layout.binary_path.chmod(0o700)
    return layout


def test_process_start_uses_loopback_dynamic_port_private_auth_and_no_secret_in_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
    captured: dict[str, object] = {}

    def fake_popen(command: list[str], **kwargs: object) -> _FakeProcess:
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        captured["popen_kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(
        "integrations.opencode.lifecycle.process.subprocess.Popen", fake_popen
    )
    monkeypatch.setattr(
        "integrations.opencode.lifecycle.process.check_health",
        lambda *args, **kwargs: HealthReport(True, "1.18.16", 200),
    )
    monkeypatch.setattr(
        OpenCodeProcessManager, "_allocate_port", staticmethod(lambda: 45678)
    )
    manager = OpenCodeProcessManager(
        layout=layout,
        settings=OpenCodeSettings(startup_timeout_seconds=1),
        manifest=ReleaseManifest.load(),
        install_manager=_Installed(layout.binary_path),  # type: ignore[arg-type]
    )

    runtime_overlay = {
        "mcp": {
            "jarvis-runtime": {
                "type": "local",
                "command": [
                    str(tmp_path / "venv" / "bin" / "python"),
                    "-m",
                    "jarvis_mcp",
                ],
                "environment": {"PYTHONPATH": str(tmp_path)},
                "enabled": True,
                "timeout": 5000,
            }
        }
    }
    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, True)
    try:
        state = manager.start(
            runtime_config_overlay=runtime_overlay,
            inherited_fds=(read_fd,),
        )
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert captured["command"] == [
        str(layout.binary_path),
        "serve",
        "--pure",
        "--hostname",
        "127.0.0.1",
        "--port",
        "45678",
    ]
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["OPENCODE_SERVER_USERNAME"] == "jarvis-opencode"
    assert len(environment["OPENCODE_SERVER_PASSWORD"]) >= 32
    assert environment["OPENCODE_SERVER_PASSWORD"] not in captured["command"]
    assert (
        json.loads(layout.opencode_config_path.read_text())["mcp"]
        == runtime_overlay["mcp"]
    )
    popen_kwargs = captured["popen_kwargs"]
    assert isinstance(popen_kwargs, dict)
    assert popen_kwargs["close_fds"] is True
    if os.name == "nt":
        assert "startupinfo" in popen_kwargs
        assert "pass_fds" not in popen_kwargs
    else:
        assert popen_kwargs["pass_fds"] == (read_fd,)
    assert state.pid == _FakeProcess.pid
    persisted = json.loads(layout.process_state_path.read_text())
    assert "password" not in persisted
    if os.name != "nt":
        assert layout.auth_state_path.stat().st_mode & 0o777 == 0o600
    assert (layout.logs_dir / "server.stdout.log").is_file()
    assert (layout.logs_dir / "server.stderr.log").is_file()


def test_process_start_rejects_non_inheritable_or_duplicate_descriptors(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    manager = OpenCodeProcessManager(
        layout=layout,
        settings=OpenCodeSettings(startup_timeout_seconds=1),
        manifest=ReleaseManifest.load(),
        install_manager=_Installed(layout.binary_path),  # type: ignore[arg-type]
    )
    read_fd, write_fd = os.pipe()
    try:
        os.set_inheritable(read_fd, False)
        with pytest.raises(ProcessManagerError, match="non héritable"):
            manager.start(inherited_fds=(read_fd,))
        os.set_inheritable(read_fd, True)
        with pytest.raises(ProcessManagerError, match="dupliqué"):
            manager.start(inherited_fds=(read_fd, read_fd))
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert not layout.process_state_path.exists()


def test_stop_refuses_a_pid_without_ownership_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
    state = ProcessState(
        pid=999999,
        port=45678,
        hostname="127.0.0.1",
        binary_path=str(layout.binary_path.resolve()),
        workspace=str(layout.integration_root),
        instance_id="instance",
        version="1.18.16",
        started_at="2026-01-01T00:00:00+00:00",
    )
    atomic_write_json(layout.process_state_path, asdict(state))
    atomic_write_json(
        layout.auth_state_path,
        {
            "instance_id": "instance",
            "username": "jarvis-opencode",
            "password": "x" * 32,
        },
    )
    manager = OpenCodeProcessManager(
        layout=layout, install_manager=_Installed(layout.binary_path)
    )  # type: ignore[arg-type]
    monkeypatch.setattr(manager, "_pid_alive", lambda _: True)
    monkeypatch.setattr(manager, "_owns_process", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "integrations.opencode.lifecycle.process.check_health",
        lambda *args, **kwargs: HealthReport(False, None, None, "network"),
    )

    with pytest.raises(ProcessOwnershipError):
        manager.stop()


def test_start_terminates_child_if_private_state_cannot_be_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        "integrations.opencode.lifecycle.process.subprocess.Popen",
        lambda *args, **kwargs: _FakeProcess(),
    )
    monkeypatch.setattr(
        "integrations.opencode.lifecycle.process.atomic_write_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setattr(
        OpenCodeProcessManager, "_allocate_port", staticmethod(lambda: 45678)
    )
    manager = OpenCodeProcessManager(
        layout=layout,
        settings=OpenCodeSettings(startup_timeout_seconds=1),
        manifest=ReleaseManifest.load(),
        install_manager=_Installed(layout.binary_path),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        manager, "_signal_group", lambda pid, sig: signals.append((pid, sig))
    )

    with pytest.raises(ProcessManagerError, match="persister"):
        manager.start()

    assert signals == [(_FakeProcess.pid, signal.SIGTERM)]
    assert not layout.process_state_path.exists()
    assert not layout.auth_state_path.exists()


def test_stop_signals_the_owned_process_group_and_removes_ephemeral_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
    state = ProcessState(
        pid=999999,
        port=45678,
        hostname="127.0.0.1",
        binary_path=str(layout.binary_path.resolve()),
        workspace=str(layout.integration_root),
        instance_id="instance",
        version="1.18.16",
        started_at="2026-01-01T00:00:00+00:00",
    )
    atomic_write_json(layout.process_state_path, asdict(state))
    atomic_write_json(
        layout.auth_state_path,
        {
            "instance_id": "instance",
            "username": "jarvis-opencode",
            "password": "x" * 32,
        },
    )
    manager = OpenCodeProcessManager(
        layout=layout, install_manager=_Installed(layout.binary_path)
    )  # type: ignore[arg-type]
    alive = [True, False]
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        manager, "_pid_alive", lambda _: alive.pop(0) if alive else False
    )
    monkeypatch.setattr(manager, "_owns_process", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        manager, "_signal_group", lambda pid, sig: signals.append((pid, sig))
    )
    monkeypatch.setattr(
        "integrations.opencode.lifecycle.process.check_health",
        lambda *args, **kwargs: HealthReport(True, "1.18.16", 200),
    )

    assert manager.stop()
    assert signals == [(999999, signal.SIGTERM)]
    assert not layout.process_state_path.exists()
    assert not layout.auth_state_path.exists()


def test_manager_cli_exposes_only_explicit_lifecycle_commands() -> None:
    parser = build_parser()
    actions = parser._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
    assert actions is not None
    assert set(actions) == {
        "install",
        "configure",
        "start",
        "stop",
        "restart",
        "status",
        "health",
        "verify",
        "smoke-test",
        "clean",
        "uninstall",
        "print-version",
    }
    assert "update" not in actions


def test_uninstall_stops_isolated_and_root_processes_before_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Installer:
        def uninstall(self) -> bool:
            events.append("remove")
            return True

    class Process:
        def stop(self) -> bool:
            events.append("stop-root")
            return True

    layout = object()
    settings = object()
    manifest = object()
    installer = Installer()
    process = Process()
    monkeypatch.setattr(
        manager_cli,
        "_components",
        lambda: (layout, settings, manifest, installer, process),
    )

    def stop_runs(**kwargs: object) -> None:
        assert kwargs == {
            "layout": layout,
            "settings": settings,
            "manifest": manifest,
            "install_manager": installer,
        }
        events.append("stop-runs")

    monkeypatch.setattr(manager_cli, "stop_isolated_run_processes", stop_runs)

    result = manager_cli.command_uninstall(object())  # type: ignore[arg-type]

    assert result == {"action": "uninstall", "changed": True, "ok": True}
    assert events == ["stop-runs", "stop-root", "remove"]


def test_health_of_idle_runtime_is_not_started_not_an_exception(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    manager = OpenCodeProcessManager(
        layout=layout,
        install_manager=_Installed(layout.binary_path),  # type: ignore[arg-type]
    )
    report = manager.health()
    status = manager.status()
    assert report.error_code == "not_started"
    assert report.healthy is False
    assert status.error_code == "not_started"


def test_start_fails_clearly_when_binary_is_invalid(tmp_path: Path) -> None:
    layout = _layout(tmp_path)

    class _Missing:
        def verify(self, *, execute_binary: bool = True) -> VerificationReport:
            return VerificationReport(
                False,
                "1.18.16",
                "darwin-arm64",
                layout.binary_path,
                ("binaire absent",),
            )

    manager = OpenCodeProcessManager(
        layout=layout,
        install_manager=_Missing(),  # type: ignore[arg-type]
    )
    with pytest.raises(Exception, match="binaire absent"):
        manager.start()


def test_start_fails_when_child_exits_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)

    class _Dead:
        pid = 17
        returncode = 9

        def poll(self) -> int:
            return 9

    monkeypatch.setattr(
        "integrations.opencode.lifecycle.process.subprocess.Popen",
        lambda *args, **kwargs: _Dead(),
    )
    manager = OpenCodeProcessManager(
        layout=layout,
        settings=OpenCodeSettings(startup_timeout_seconds=1),
        manifest=ReleaseManifest.load(),
        install_manager=_Installed(layout.binary_path),  # type: ignore[arg-type]
    )
    with pytest.raises(ProcessManagerError, match="code 9"):
        manager.start()


def test_start_times_out_when_health_never_arrives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
    monkeypatch.setattr(
        "integrations.opencode.lifecycle.process.subprocess.Popen",
        lambda *args, **kwargs: _FakeProcess(),
    )
    monkeypatch.setattr(
        "integrations.opencode.lifecycle.process.check_health",
        lambda *args, **kwargs: HealthReport(False, None, None, "startup"),
    )
    monkeypatch.setattr(OpenCodeProcessManager, "stop", lambda self, **kwargs: True)
    manager = OpenCodeProcessManager(
        layout=layout,
        settings=OpenCodeSettings(startup_timeout_seconds=0.2),
        manifest=ReleaseManifest.load(),
        install_manager=_Installed(layout.binary_path),  # type: ignore[arg-type]
    )
    with pytest.raises(ProcessManagerError, match="Health-check OpenCode expiré"):
        manager.start()

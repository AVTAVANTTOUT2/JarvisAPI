"""Régressions du finding F-P08-01 (`computer.run` confiné).

Le finding F-P08-02 visait le moteur Open Interpreter dormant : la
bibliothèque ayant été retirée, ses contrats vivent désormais dans
`tests/test_no_open_interpreter.py`.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest


def test_powerful_execution_capabilities_are_boolean_and_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Les valeurs par défaut doivent rester booléennes et fail-closed."""
    import config
    import env_loader

    monkeypatch.delenv("COMPUTER_ACCESS", raising=False)
    monkeypatch.setattr(env_loader, "load_jarvis_env", lambda: None)

    spec = importlib.util.spec_from_file_location("config_p08_defaults", config.__file__)
    assert spec is not None and spec.loader is not None
    isolated_config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated_config)

    assert isolated_config.COMPUTER_ACCESS is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bypass",
    [
        'rm -rf "$HOME"',
        "python3 -c 'import shutil; shutil.rmtree(\"/\")'",
        'osascript -e \'do shell script "rm -rf /"\'',
        "command rm -rf /",
    ],
)
async def test_legacy_run_rejects_denylist_bypasses_without_spawning(
    monkeypatch: pytest.MonkeyPatch,
    bypass: str,
) -> None:
    from integrations.computer import ComputerControl

    control = ComputerControl()
    control.allowed = True

    async def forbidden_spawn(*args, **kwargs):
        raise AssertionError(f"subprocess inattendu: {args!r} {kwargs!r}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_spawn)
    result = await control.run(bypass)

    assert result["ok"] is False
    assert "run(str) est désactivé" in result["error"]


@pytest.mark.asyncio
async def test_child_process_receives_only_minimal_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.computer import ComputerControl

    captured: dict = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, input=None):
            del input
            return b"Now drawing from 'AC Power'\n -InternalBattery-0 100%; charged", b""

    async def fake_spawn(*argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-leak")
    monkeypatch.setenv("LOCATION_API_TOKEN", "must-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-leak")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)

    control = ComputerControl()
    control.allowed = True
    result = await control.get_battery()

    assert result["battery_percent"] == 100
    assert captured["argv"] == ("/usr/bin/pmset", "-g", "batt")
    assert set(captured["env"]) == {
        "PATH",
        "HOME",
        "USER",
        "TMPDIR",
        "LANG",
        "LC_ALL",
    }
    assert "DEEPSEEK_API_KEY" not in captured["env"]
    assert "LOCATION_API_TOKEN" not in captured["env"]
    assert "AWS_SECRET_ACCESS_KEY" not in captured["env"]


def test_computer_module_contains_no_shell_subprocess() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "integrations" / "computer.py"
    ).read_text(encoding="utf-8")
    assert "create_subprocess_shell" not in source

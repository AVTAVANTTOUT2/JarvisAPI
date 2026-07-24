"""Smoke tests exécutés sur le runner macOS sans accès aux données utilisateur."""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from integrations._applescript import run_applescript, run_applescript_async


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="contrats du runtime macOS",
)


def test_required_macos_commands_are_available():
    commands = (
        "afinfo",
        "afplay",
        "launchctl",
        "open",
        "osacompile",
        "osascript",
        "plutil",
        "say",
        "screencapture",
    )

    missing = [command for command in commands if shutil.which(command) is None]

    assert missing == []


def test_osascript_helper_executes_pure_applescript():
    result = run_applescript('return "jarvis-ci"')

    assert result.ok is True
    assert result.reason == "ok"
    assert result.stdout == "jarvis-ci"


@pytest.mark.asyncio
async def test_async_osascript_helper_executes_pure_applescript():
    result = await run_applescript_async('return 6 * 7')

    assert result.ok is True
    assert result.stdout == "42"


def test_apple_application_dictionaries_compile(tmp_path):
    scripts = {
        "mail": 'tell application "Mail" to return name',
        "calendar": 'tell application "Calendar" to return name',
        "contacts": 'tell application "Contacts" to return name',
        "messages": 'tell application "Messages" to return name',
    }

    for name, script in scripts.items():
        output = tmp_path / f"{name}.scpt"
        result = subprocess.run(
            ["osacompile", "-o", str(output), "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert output.stat().st_size > 0


def test_launch_agent_plists_are_valid():
    plists = (
        ROOT / "com.jarvis.imessage-daemon.plist",
        ROOT / "com.jarvis.supervisor.plist",
        ROOT / "tv" / "com.jarvis.tv-browser.plist",
        ROOT / "tv" / "com.jarvis.tv.plist",
    )

    for path in plists:
        result = subprocess.run(
            ["plutil", "-lint", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        payload = plistlib.loads(path.read_bytes())
        assert payload["Label"].startswith("com.jarvis.")
        assert payload["ProgramArguments"]
        assert payload["RunAtLoad"] is True


def test_say_synthesizes_audio_and_coreaudio_is_visible(tmp_path):
    output = tmp_path / "jarvis-ci.aiff"
    synthesis = subprocess.run(
        [
            "say",
            "-o",
            str(output),
            "Jarvis continuous integration",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert synthesis.returncode == 0, synthesis.stderr
    assert output.stat().st_size > 44
    audio_info = subprocess.run(
        ["afinfo", str(output)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert audio_info.returncode == 0, audio_info.stderr

    import sounddevice

    host_apis = sounddevice.query_hostapis()
    assert any("Core Audio" in str(api.get("name", "")) for api in host_apis)

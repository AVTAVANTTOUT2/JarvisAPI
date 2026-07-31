"""Régressions de génération des LaunchAgents sans chemin utilisateur figé."""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.launchagents import write_launch_agents

ROOT = Path(__file__).resolve().parents[1]


def _fake_checkout(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "Checkout JARVIS avec espaces"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    (repo / "supervisor.py").write_text("# supervisor\n", encoding="utf-8")
    (scripts / "imessage_daemon.py").write_text("# daemon\n", encoding="utf-8")
    python = repo / "venv backend" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    return repo, python.parent.parent


def test_cli_generates_both_plists_in_paths_with_spaces(tmp_path: Path) -> None:
    repo, venv = _fake_checkout(tmp_path)
    output = tmp_path / "Launch Agents générés"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "launchagents.py"),
            "generate",
            "--repo-root",
            str(repo),
            "--venv",
            str(venv),
            "--output-dir",
            str(output),
            "--service",
            "supervisor",
            "--service",
            "imessage-daemon",
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    expected = {
        "com.jarvis.supervisor.plist": repo / "supervisor.py",
        "com.jarvis.imessage-daemon.plist": repo / "scripts" / "imessage_daemon.py",
    }
    for filename, entrypoint in expected.items():
        path = output / filename
        payload = plistlib.loads(path.read_bytes())
        assert payload["ProgramArguments"][0] == str(venv / "bin" / "python")
        assert payload["ProgramArguments"][1] == str(entrypoint)
        assert payload["WorkingDirectory"] == str(repo)
        assert payload["StandardOutPath"].startswith(str(repo / "data" / "logs"))
        assert payload["StandardErrorPath"].startswith(str(repo / "data" / "logs"))
        assert "/Users/zeldris/JarvisAPI" not in path.read_text(encoding="utf-8")


def test_generation_fails_when_backend_venv_is_missing(tmp_path: Path) -> None:
    repo, _venv = _fake_checkout(tmp_path)

    with pytest.raises(FileNotFoundError, match="Répertoire introuvable"):
        write_launch_agents(
            output_dir=tmp_path / "out",
            repo_root=repo,
            venv_dir=repo / "venv-absent",
            services=("supervisor",),
        )

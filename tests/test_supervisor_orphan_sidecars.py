"""Nettoyage des sidecars TTS orphelins au démarrage/arrêt backend.

Le superviseur doit scanner **tous** les launchers listés (pas seulement le
dernier ``pgrep``), et le SIGKILL de suivi doit porter sur la même liste
agrégée — sinon un moteur basculé laisse ses poids Metal en mémoire.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_tts_sidecar_scripts_include_production_launcher():
    import supervisor

    assert "qwen3_local.py" in supervisor._TTS_SIDECAR_SCRIPTS
    # Chaque entrée doit correspondre à un launcher versionné du dépôt.
    for script in supervisor._TTS_SIDECAR_SCRIPTS:
        assert (PROJECT_ROOT / "native_audio" / script).is_file(), script


def test_kill_orphan_tts_sidecars_scans_every_listed_script(monkeypatch):
    import supervisor

    monkeypatch.setattr(
        supervisor,
        "_TTS_SIDECAR_SCRIPTS",
        ("qwen3_local.py", "fish_local.py"),
    )
    monkeypatch.setattr(supervisor, "_managed_pids", lambda: set())
    monkeypatch.setattr(supervisor.time, "sleep", lambda *_a, **_k: None)

    pgrep_markers: list[str] = []
    killed: list[tuple[int, int]] = []

    def fake_run(cmd, *args, **kwargs):
        if cmd[:2] == ["pgrep", "-f"]:
            pgrep_markers.append(cmd[2])
            # Un PID distinct par launcher pour vérifier l'agrégation.
            if cmd[2].endswith("qwen3_local.py"):
                return SimpleNamespace(stdout="9001\n", returncode=0)
            return SimpleNamespace(stdout="9002\n", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    def fake_check_output(cmd, *args, **kwargs):
        # ppid hors arbre géré → candidat orphelin.
        return "1\n"

    def fake_kill_tree(pid, *, sig):
        killed.append((pid, int(sig)))

    # Après SIGTERM, les deux PIDs résistent encore → SIGKILL attendu.
    alive = {9001, 9002}

    def fake_kill(pid, sig):
        if sig == 0:
            if pid not in alive:
                raise ProcessLookupError(pid)
            return None
        raise AssertionError("os.kill ne doit servir qu'à sonder (sig=0)")

    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)
    monkeypatch.setattr(supervisor.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(supervisor, "_kill_process_tree", fake_kill_tree)
    monkeypatch.setattr(supervisor.os, "kill", fake_kill)

    count = supervisor._kill_orphan_tts_sidecars()

    assert count == 2
    assert any(m.endswith("qwen3_local.py") for m in pgrep_markers)
    assert any(m.endswith("fish_local.py") for m in pgrep_markers)
    term_pids = sorted(pid for pid, sig in killed if sig == supervisor.signal.SIGTERM)
    kill_pids = sorted(pid for pid, sig in killed if sig == supervisor.signal.SIGKILL)
    assert term_pids == [9001, 9002]
    assert kill_pids == [9001, 9002], (
        "le suivi SIGKILL doit rejouer la liste agrégée, pas le dernier pgrep"
    )


def test_kill_orphan_tts_sidecars_spares_managed_children(monkeypatch):
    import supervisor

    monkeypatch.setattr(supervisor, "_TTS_SIDECAR_SCRIPTS", ("qwen3_local.py",))
    monkeypatch.setattr(supervisor, "_managed_pids", lambda: {42})
    monkeypatch.setattr(supervisor.time, "sleep", lambda *_a, **_k: None)

    def fake_run(cmd, *args, **kwargs):
        if cmd[:2] == ["pgrep", "-f"]:
            return SimpleNamespace(stdout="9003\n", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)
    monkeypatch.setattr(
        supervisor.subprocess,
        "check_output",
        lambda *a, **k: "42\n",
    )
    with patch.object(supervisor, "_kill_process_tree") as kill_tree:
        count = supervisor._kill_orphan_tts_sidecars()

    assert count == 0
    kill_tree.assert_not_called()

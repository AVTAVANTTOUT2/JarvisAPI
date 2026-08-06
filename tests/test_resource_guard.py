"""Garde-fou RAM / process — politique A (JARVIS only)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jarvis.resource_guard import (
    GuardConfig,
    ProcessInfo,
    ResourceGuard,
    classify_cmdline,
    parse_memory_pressure,
    plan_actions,
)

PROJECT = Path("/Users/zeldris/JARVIS")


def _cfg(**overrides) -> GuardConfig:
    base = dict(
        enabled=True,
        warn_free_mb=2048,
        critical_free_mb=1024,
        ollama_idle_stop=True,
        ollama_idle_ttl_s=120.0,
        tts_max_workers=1,
        kill_orphans=True,
        dry_run=False,
        project_dir=PROJECT,
    )
    base.update(overrides)
    return GuardConfig(**base)


def test_classify_rejects_non_jarvis_processes() -> None:
    assert classify_cmdline("node /Applications/Cursor.app/foo", PROJECT) is None
    assert classify_cmdline("python3.13 /tmp/codex worker", PROJECT) is None
    assert classify_cmdline("Google Chrome Helper", PROJECT) is None
    assert classify_cmdline("Claude", PROJECT) is None
    # Même nom de script hors dépôt → ignore
    assert classify_cmdline("/tmp/other/native_audio/qwen3_local.py --serve", PROJECT) is None


def test_classify_accepts_jarvis_markers() -> None:
    tts = f"{PROJECT}/native_audio/qwen3_local.py --serve"
    assert classify_cmdline(tts, PROJECT) == "tts_sidecar"
    assert classify_cmdline(f"Python {PROJECT}/scripts/audio_daemon.py", PROJECT) == "audio_daemon"
    assert classify_cmdline("ollama serve", PROJECT) == "ollama_serve"
    assert (
        classify_cmdline(
            "/opt/homebrew/bin/ollama runner --model /Users/zeldris/.ollama/models/blobs/x",
            PROJECT,
        )
        == "ollama_runner"
    )
    assert (
        classify_cmdline(
            "llama-server --model /Users/zeldris/.ollama/models/blobs/abc",
            PROJECT,
        )
        == "ollama_runner"
    )


def test_parse_memory_pressure_free_mb() -> None:
    sample = """
The system has 34359738368 (2097152 pages with a page size of 16384).
Pages free: 40000
Pages purgeable: 10000
"""
    snap = parse_memory_pressure(sample)
    assert snap.page_size == 16384
    assert snap.pages_free == 40000
    assert snap.pages_purgeable == 10000
    # (50000 * 16384) / 1024 / 1024 = 781.25
    assert snap.free_mb == pytest.approx(781.25, rel=1e-3)


def test_plan_kills_orphan_tts_but_spares_managed() -> None:
    procs = [
        ProcessInfo(9001, 1, 8000000, f"{PROJECT}/native_audio/qwen3_local.py --serve", "tts_sidecar"),
        ProcessInfo(9002, 42, 8000000, f"{PROJECT}/native_audio/qwen3_local.py --serve", "tts_sidecar"),
    ]
    actions = plan_actions(
        procs,
        managed_pids={42, 100},
        free_mb=5000.0,
        config=_cfg(),
        screen_watcher_running=True,
        ollama_idle_s=0.0,
    )
    kinds = {(a.action, a.pid) for a in actions}
    assert ("kill_orphan_tts", 9001) in kinds
    assert ("kill_orphan_tts", 9002) not in kinds


def test_plan_caps_duplicate_tts_workers() -> None:
    procs = [
        ProcessInfo(9001, 42, 1000, f"{PROJECT}/native_audio/qwen3_local.py --serve", "tts_sidecar"),
        ProcessInfo(9002, 42, 1000, f"{PROJECT}/native_audio/qwen3_local.py --serve", "tts_sidecar"),
    ]
    actions = plan_actions(
        procs,
        managed_pids={42},
        free_mb=5000.0,
        config=_cfg(tts_max_workers=1),
        screen_watcher_running=True,
        ollama_idle_s=0.0,
    )
    # Garde le PID le plus bas, tue le surplus.
    assert any(a.action == "kill_duplicate_tts" and a.pid == 9002 for a in actions)
    assert not any(a.pid == 9001 for a in actions)


def test_plan_stops_ollama_only_when_idle_and_sw_stopped() -> None:
    procs = [
        ProcessInfo(1145, 1, 40000, "ollama serve", "ollama_serve"),
    ]
    # SW running → pas de stop
    actions = plan_actions(
        procs, {1}, 500.0, _cfg(), screen_watcher_running=True, ollama_idle_s=999.0
    )
    assert not any(a.action == "stop_ollama_idle" for a in actions)

    # SW stopped but TTL not reached
    actions = plan_actions(
        procs, {1}, 500.0, _cfg(), screen_watcher_running=False, ollama_idle_s=30.0
    )
    assert not any(a.action == "stop_ollama_idle" for a in actions)

    # TTL ok + critical free
    actions = plan_actions(
        procs, {1}, 500.0, _cfg(), screen_watcher_running=False, ollama_idle_s=200.0
    )
    assert any(a.action == "stop_ollama_idle" for a in actions)


def test_plan_never_emits_actions_for_foreign_pids() -> None:
    # kind hors allowlist : aucun kill, même sous pression critique.
    procs = [
        ProcessInfo(1, 0, 999999, "Codex python3.13 worker", "foreign"),
    ]
    actions = plan_actions(
        procs, set(), 100.0, _cfg(), screen_watcher_running=False, ollama_idle_s=999.0
    )
    assert actions == []
    assert classify_cmdline("Codex python3.13 worker", PROJECT) is None


def test_guard_tick_dry_run_does_not_kill() -> None:
    killed: list[int] = []
    stopped: list[str] = []

    guard = ResourceGuard(
        config=_cfg(dry_run=True),
        list_processes=lambda: [
            ProcessInfo(
                9001,
                1,
                1000,
                f"{PROJECT}/native_audio/qwen3_local.py --serve",
                "tts_sidecar",
            )
        ],
        read_free_mb=lambda: 5000.0,
        is_screen_watcher_running=lambda: True,
        managed_pids=lambda: set(),
        kill_process_tree=lambda pid, *, sig: killed.append(pid),
        stop_ollama=lambda: stopped.append("ollama") or {"ok": True},
        monotonic=lambda: 1000.0,
    )
    report = guard.tick()
    assert killed == []
    assert stopped == []
    assert report.actions
    assert all(a.dry_run for a in report.actions)
    assert report.level == "ok"


def test_guard_tracks_ollama_idle_across_ticks() -> None:
    now = {"t": 0.0}
    ollama_calls: list[str] = []

    guard = ResourceGuard(
        config=_cfg(ollama_idle_ttl_s=100.0, critical_free_mb=9000),
        list_processes=lambda: [
            ProcessInfo(1145, 1, 1000, "ollama serve", "ollama_serve"),
        ],
        read_free_mb=lambda: 100.0,  # critical
        is_screen_watcher_running=lambda: now["t"] < 50,
        managed_pids=lambda: {1},
        kill_process_tree=lambda pid, *, sig: None,
        stop_ollama=lambda: ollama_calls.append("stop") or {"ok": True},
        monotonic=lambda: now["t"],
    )

    now["t"] = 10.0
    r1 = guard.tick()
    assert not any(a.action == "stop_ollama_idle" for a in r1.actions)

    now["t"] = 60.0  # SW stopped, idle = 0 start
    r2 = guard.tick()
    assert not any(a.action == "stop_ollama_idle" and a.executed for a in r2.actions)

    now["t"] = 170.0  # idle 110s >= 100
    r3 = guard.tick()
    assert ollama_calls == ["stop"]
    assert any(a.action == "stop_ollama_idle" and a.executed for a in r3.actions)


def test_plan_kills_duplicate_audio_daemon_orphan() -> None:
    procs = [
        ProcessInfo(10, 42, 100, f"{PROJECT}/scripts/audio_daemon.py", "audio_daemon"),
        ProcessInfo(11, 1, 100, f"{PROJECT}/scripts/audio_daemon.py", "audio_daemon"),
    ]
    actions = plan_actions(
        procs, {42}, 5000.0, _cfg(), screen_watcher_running=True, ollama_idle_s=0.0
    )
    assert any(a.action == "kill_duplicate_daemon" and a.pid == 11 for a in actions)
    assert not any(a.pid == 10 for a in actions)


def test_plan_kills_surplus_managed_daemon_duplicates() -> None:
    """Deux daemons gérés du même kind : un seul survit."""
    procs = [
        ProcessInfo(10, 42, 100, f"{PROJECT}/scripts/audio_daemon.py", "audio_daemon"),
        ProcessInfo(11, 42, 100, f"{PROJECT}/scripts/audio_daemon.py", "audio_daemon"),
        ProcessInfo(12, 42, 100, f"{PROJECT}/scripts/audio_daemon.py", "audio_daemon"),
    ]
    actions = plan_actions(
        procs, {42}, 5000.0, _cfg(), screen_watcher_running=True, ollama_idle_s=0.0
    )
    killed = {a.pid for a in actions if a.action == "kill_duplicate_daemon"}
    assert killed == {11, 12}


def test_snapshot_never_executes_actions_nor_moves_the_tick_clock() -> None:
    """La lecture HTTP observe : elle ne tue rien et n'arme aucune horloge."""
    killed: list[int] = []
    stopped: list[str] = []
    guard = ResourceGuard(
        config=_cfg(ollama_idle_ttl_s=0.0),
        list_processes=lambda: [
            ProcessInfo(1145, 1, 1000, "ollama serve", "ollama_serve"),
            ProcessInfo(
                9001,
                1,
                1000,
                f"{PROJECT}/native_audio/qwen3_local.py --serve",
                "tts_sidecar",
            ),
        ],
        read_free_mb=lambda: 100.0,
        is_screen_watcher_running=lambda: False,
        managed_pids=lambda: set(),
        kill_process_tree=lambda pid, *, sig: killed.append(pid),
        stop_ollama=lambda: stopped.append("ollama") or {"ok": True},
        monotonic=lambda: 1000.0,
    )

    report = guard.snapshot()

    assert killed == []
    assert stopped == []
    assert report.actions, "le relevé doit annoncer ce que le tick ferait"
    assert all(not a.executed for a in report.actions)
    # Ni l'intervalle du tick périodique ni le compte à rebours Ollama ne
    # doivent bouger sous l'effet d'une consultation.
    assert guard.should_tick(1.0) is True
    assert guard.last_report is None
    assert report.ollama_idle_seconds == 0.0

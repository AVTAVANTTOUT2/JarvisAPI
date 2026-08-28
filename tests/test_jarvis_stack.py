"""Contrats du cycle de vie CLI JARVIS : appartenance, stop, restart.

Le classifieur doit arrêter tout le stack JARVIS (Python, Node Claw3D, Ollama,
OpenCode, daemons) sans jamais toucher Cursor, ChatGPT, pytest, ni le CLI
``jarvis stop`` lui-même.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import signal as signalmod
import threading
import time

import pytest

from scripts.jarvis_stack import (
    LAUNCHD_LABELS,
    ProcessSnapshot,
    RestartBlocked,
    classify_port_occupancy,
    classify_process,
    cli_restart,
    prepare_supervisor_bind,
    select_owned,
    stop_stack,
    wait_after_stop,
    wait_predicate,
)


ROOT = Path("/Users/example/JARVIS")


def _proc(
    pid: int,
    command: str,
    *,
    ppid: int = 1,
    cwd: str | None = None,
) -> ProcessSnapshot:
    return ProcessSnapshot(pid=pid, ppid=ppid, command=command, cwd=cwd)


OWNED_CASES = (
    (
        "supervisor",
        _proc(10, "/opt/homebrew/bin/python3.12 supervisor.py", cwd=str(ROOT)),
    ),
    (
        "backend",
        _proc(11, f"/opt/homebrew/bin/python3.12 {ROOT}/main.py", cwd=str(ROOT)),
    ),
    (
        "backend",
        _proc(12, "/opt/homebrew/bin/python3.12 main.py", cwd=str(ROOT)),
    ),
    (
        "imessage_daemon",
        _proc(13, f"Python {ROOT}/scripts/imessage_daemon.py --port 8193"),
    ),
    (
        "tts",
        _proc(14, f"Python {ROOT}/native_audio/qwen3_local.py --serve"),
    ),
    (
        "audio_daemon",
        _proc(15, f"Python {ROOT}/scripts/audio_daemon.py"),
    ),
    (
        "tv_dashboard",
        _proc(16, f"Python {ROOT}/tv/server.py"),
    ),
    (
        "jarvis_agent",
        _proc(
            17, f"Python {ROOT}/scripts/jarvis_agent.py --server https://127.0.0.1:8081"
        ),
    ),
    (
        "ollama",
        _proc(18, "ollama serve"),
    ),
    (
        "ollama",
        _proc(22, "/opt/homebrew/bin/ollama serve"),
    ),
    (
        "claw3d",
        _proc(
            19,
            "next-server (v16.1.7)",
            cwd=f"{ROOT}/.jarvis/apps/claw3d/apps/claw3d-ui",
        ),
    ),
    (
        "claw3d",
        _proc(
            20,
            "next-server (v16.1.7) CLAW3D_STATE_ROOT=/tmp/.claw3d",
            cwd=f"{ROOT}/.jarvis/apps/claw3d",
        ),
    ),
    (
        "frontend_dev",
        _proc(
            21,
            "node /Users/example/JARVIS/frontend/node_modules/next/dist/bin/next",
            cwd=f"{ROOT}/frontend",
        ),
    ),
)


PROTECTED_CASES = (
    _proc(100, "Cursor Helper (Plugin): extension-host JARVIS [1-88]"),
    _proc(
        101,
        "cursor-agent --worker-dir /Users/example/JARVIS --name ~/JARVIS",
        cwd=str(ROOT),
    ),
    _proc(
        102,
        "/Applications/ChatGPT.app/Contents/Resources/cua_node/bin/node --working-dir /Users/example/JARVIS",
        cwd=str(ROOT),
    ),
    _proc(103, "python -m pytest tests/test_jarvis_stack.py", cwd=str(ROOT)),
    _proc(104, f"Python {ROOT}/scripts/jarvis_launchd.py stop", cwd=str(ROOT)),
    _proc(
        105,
        f"Python {ROOT}/venv/bin/python {ROOT}/scripts/jarvis_stack.py",
        cwd=str(ROOT),
    ),
    _proc(106, "/usr/bin/python3 /other/project/main.py", cwd="/other/project"),
    _proc(107, "node /usr/local/bin/vite", cwd="/Users/example/other-app"),
    _proc(108, "postgres"),
    _proc(109, "caffeinate -d -i", ppid=1),
)


@pytest.mark.parametrize("service,snapshot", OWNED_CASES)
def test_classify_owns_jarvis_stack_processes(service: str, snapshot: ProcessSnapshot):
    assert classify_process(snapshot, ROOT) == service


@pytest.mark.parametrize("snapshot", PROTECTED_CASES)
def test_classify_never_owns_foreign_or_self(snapshot: ProcessSnapshot):
    assert classify_process(snapshot, ROOT) is None


def test_select_owned_includes_supervisor_caffeinate_child():
    supervisor = _proc(50, "Python supervisor.py", cwd=str(ROOT))
    child = _proc(51, "caffeinate -dims -t 0", ppid=50)
    foreign = _proc(52, "caffeinate -dims -t 0", ppid=1)
    owned = select_owned([supervisor, child, foreign], ROOT)
    assert {item.pid for item in owned} == {50, 51}
    assert {item.service for item in owned} == {"supervisor", "caffeinate"}


def test_select_owned_keeps_orphan_caffeinate_after_supervisor_is_listed():
    """Un caffeinate orphelin (ppid 1) n'est pris que s'il n'y a plus de parent,
    jamais tout seul sur la machine."""
    orphan = _proc(61, "caffeinate -dims -t 0", ppid=1)
    owned = select_owned([orphan], ROOT)
    assert owned == []


def test_launchd_labels_cover_known_agents():
    assert "com.jarvis.supervisor" in LAUNCHD_LABELS
    assert "com.jarvis.ingestion" in LAUNCHD_LABELS


def test_stop_stack_bootouts_then_signals_owned_never_foreign():
    snapshots = [
        _proc(10, "Python supervisor.py", cwd=str(ROOT)),
        _proc(11, "ollama serve", ppid=10),
        _proc(12, "Cursor Helper (Plugin): extension-host JARVIS"),
        _proc(13, f"Python {ROOT}/scripts/jarvis_launchd.py stop", cwd=str(ROOT)),
    ]
    signals: list[tuple[int, int]] = []
    bootouts: list[str] = []
    managers: list[str] = []
    alive = {10, 11, 12, 13}

    def bootout() -> list[str]:
        bootouts.append("done")
        return ["com.jarvis.supervisor"]

    def stop_managers() -> dict[str, object]:
        managers.append("agentic_runtime")
        return {
            "agentic_runtime": {"ok": True},
            "claw3d": {"ok": True},
            "ollama": {"ok": True},
        }

    def signal_pid(pid: int, sig: int) -> None:
        signals.append((pid, sig))
        if sig != 0:
            alive.discard(pid)

    def pid_alive(pid: int) -> bool:
        return pid in alive

    report = stop_stack(
        root=ROOT,
        snapshots=snapshots,
        bootout=bootout,
        stop_managers=stop_managers,
        signal_pid=signal_pid,
        pid_alive=pid_alive,
        sleep=lambda _s: None,
    )

    assert bootouts == ["done"]
    assert managers == ["agentic_runtime"]
    signaled = {pid for pid, _sig in signals}
    assert signaled == {10, 11}
    assert 12 not in signaled
    assert 13 not in signaled
    assert report.ok
    assert {item.pid for item in report.stopped} == {10, 11}


def test_stop_stack_escalates_to_sigkill_when_term_ignored():
    snapshots = [_proc(10, "Python supervisor.py", cwd=str(ROOT))]
    signals: list[int] = []

    def signal_pid(pid: int, sig: int) -> None:
        signals.append(sig)
        if sig == 0:
            return

    report = stop_stack(
        root=ROOT,
        snapshots=snapshots,
        bootout=lambda: [],
        stop_managers=lambda: {},
        signal_pid=signal_pid,
        pid_alive=lambda _pid: True,
        sleep=lambda _s: None,
    )

    import signal as signalmod

    assert signalmod.SIGTERM in signals
    assert signalmod.SIGKILL in signals
    assert report.ok is False
    assert report.still_alive == [10]


def test_cli_wrapper_exposes_stop_start_restart():
    wrapper = Path(__file__).resolve().parents[1] / "scripts" / "jarvis"
    text = wrapper.read_text(encoding="utf-8")
    for command in ("stop", "start", "restart", "maj", "status"):
        assert command in text


def test_launchd_cli_maps_stop_start_and_maj_alias():
    from scripts import jarvis_launchd as launchd

    assert "stop" in launchd.COMMANDS
    assert "start" in launchd.COMMANDS
    assert launchd.COMMANDS["maj"] is launchd.COMMANDS["restart"]
    assert launchd.COMMANDS["restart"] is not launchd.cmd_stop


def test_restart_stops_before_starting(monkeypatch):
    from scripts import jarvis_launchd as launchd
    import scripts.jarvis_stack as stack

    order: list[str] = []
    monkeypatch.setattr(launchd, "cmd_stop", lambda: order.append("stop") or 0)
    monkeypatch.setattr(launchd, "cmd_start", lambda: order.append("start") or 0)
    monkeypatch.setattr(stack, "wait_after_stop", lambda **_kwargs: None)

    @contextmanager
    def _mutex(**_kwargs):
        yield

    monkeypatch.setattr(stack, "acquire_cli_mutex", _mutex)

    assert launchd.cmd_restart() == 0
    assert order == ["stop", "start"]


def test_restart_refuses_when_stop_fails(monkeypatch):
    from scripts import jarvis_launchd as launchd
    import scripts.jarvis_stack as stack

    order: list[str] = []
    monkeypatch.setattr(launchd, "cmd_stop", lambda: 1)
    monkeypatch.setattr(launchd, "cmd_start", lambda: order.append("start") or 0)
    monkeypatch.setattr(stack, "wait_after_stop", lambda **_kwargs: None)

    @contextmanager
    def _mutex(**_kwargs):
        yield

    monkeypatch.setattr(stack, "acquire_cli_mutex", _mutex)

    assert launchd.cmd_restart() == 1
    assert order == []


def test_install_copies_cli_wrapper(tmp_path, monkeypatch):
    from scripts import jarvis_launchd as launchd

    dest = tmp_path / "bin" / "jarvis"
    monkeypatch.setattr(launchd, "CLI_DEST", dest)
    monkeypatch.setattr(launchd, "_install_launchd_plist", lambda: None)
    monkeypatch.setattr(launchd, "_install_app", lambda: None)
    monkeypatch.setattr(launchd, "_bootstrap", lambda: True)

    assert launchd.cmd_install() == 0
    assert dest.is_file()
    assert dest.stat().st_mode & 0o111
    text = dest.read_text(encoding="utf-8")
    assert "jarvis stop" in text or "stop|" in text


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_stop_stack_does_not_sigkill_a_reused_pid():
    snapshots = [_proc(10, "Python supervisor.py", cwd=str(ROOT))]
    signals: list[int] = []

    def list_snapshots():
        if signalmod.SIGTERM in signals:
            return [_proc(10, "postgres")]
        return snapshots

    def signal_pid(_pid: int, sig: int) -> None:
        signals.append(sig)

    report = stop_stack(
        root=ROOT,
        snapshots=snapshots,
        list_snapshots=list_snapshots,
        bootout=lambda: [],
        stop_managers=lambda: {},
        signal_pid=signal_pid,
        pid_alive=lambda _pid: True,
        sleep=lambda _s: None,
    )

    assert signalmod.SIGTERM in signals
    assert signalmod.SIGKILL not in signals
    assert report.still_alive == []


def test_wait_after_stop_refuses_foreign_port_occupant():
    clock = _Clock()
    with pytest.raises(RestartBlocked, match="tiers"):
        wait_after_stop(
            root=ROOT,
            ports=(9000,),
            lock_held=lambda _path: False,
            list_listeners=lambda _port: (4242,),
            list_snapshots=lambda: [_proc(1, "postgres")],
            sleep=clock.sleep,
            clock=clock,
            lock_timeout_s=1.0,
            port_timeout_s=1.0,
        )


def test_wait_after_stop_accepts_dead_pid_and_stale_lock():
    clock = _Clock()
    wait_after_stop(
        root=ROOT,
        ports=(9000,),
        lock_held=lambda _path: False,
        list_listeners=lambda _port: (),
        list_snapshots=lambda: [],
        sleep=clock.sleep,
        clock=clock,
        lock_timeout_s=1.0,
        port_timeout_s=1.0,
    )


def test_wait_after_stop_waits_for_slow_owned_listener_then_succeeds():
    clock = _Clock()

    def list_listeners(_port: int) -> tuple[int, ...]:
        return (10,) if clock.now < 0.5 else ()

    wait_after_stop(
        root=ROOT,
        ports=(9000,),
        lock_held=lambda _path: False,
        list_listeners=list_listeners,
        list_snapshots=lambda: [_proc(10, "Python supervisor.py", cwd=str(ROOT))],
        sleep=clock.sleep,
        clock=clock,
        lock_timeout_s=2.0,
        port_timeout_s=2.0,
    )
    assert clock.now >= 0.5


def test_prepare_supervisor_bind_never_signals_foreign_pid():
    clock = _Clock()
    with pytest.raises(RestartBlocked, match="tiers"):
        prepare_supervisor_bind(
            ROOT,
            9000,
            lock_held=lambda _path: False,
            list_listeners=lambda _port: (77,),
            list_snapshots=lambda: [_proc(77, "nginx")],
            sleep=clock.sleep,
            clock=clock,
            lock_timeout_s=0.2,
            port_timeout_s=0.2,
        )


def test_cli_restart_twenty_sequential_cycles():
    starts: list[int] = []

    @contextmanager
    def mutex():
        yield

    for index in range(20):
        code = cli_restart(
            root=ROOT,
            stop=lambda: 0,
            start=lambda: starts.append(1) or 0,
            wait=lambda **_kwargs: None,
            mutex=mutex,
        )
        assert code == 0, index
    assert len(starts) == 20


def test_cli_restart_serializes_five_concurrent_requests():
    current = 0
    peak = 0
    guard = threading.Lock()
    serial = threading.Lock()

    @contextmanager
    def mutex():
        serial.acquire()
        try:
            yield
        finally:
            serial.release()

    def start() -> int:
        nonlocal current, peak
        with guard:
            current += 1
            peak = max(peak, current)
        time.sleep(0.02)
        with guard:
            current -= 1
        return 0

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            cli_restart(
                root=ROOT,
                stop=lambda: 0,
                start=start,
                wait=lambda **_kwargs: None,
                mutex=mutex,
            )
        except BaseException as exc:  # noqa: BLE001 - collect for the parent thread
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert errors == []
    assert peak == 1


def test_launch_supervisor_script_never_kills_by_port():
    script = Path(__file__).resolve().parents[1] / "scripts" / "launch_supervisor.sh"
    text = script.read_text(encoding="utf-8")
    assert "prepare_supervisor_bind" in text
    assert "xargs kill" not in text
    assert "kill -KILL" not in text
    assert "kill -TERM" not in text


def test_classify_port_occupancy_splits_owned_and_foreign():
    owned, foreign = classify_port_occupancy(
        9000,
        root=ROOT,
        list_listeners=lambda _port: (10, 11),
        list_snapshots=lambda: [
            _proc(10, "Python supervisor.py", cwd=str(ROOT)),
            _proc(11, "nginx"),
        ],
    )
    assert owned == (10,)
    assert foreign == (11,)


def test_wait_predicate_is_bounded():
    clock = _Clock()
    assert (
        wait_predicate(lambda: False, timeout_s=0.3, sleep=clock.sleep, clock=clock)
        is False
    )
    assert clock.now >= 0.3

"""Cycle de vie du stack JARVIS : appartenance des processus et arrêt complet.

Ce module ne tue jamais un processus sur le seul nom ``python`` / ``node``.
Il n'appartient un PID que s'il matche un entrypoint du checkout, Ollama géré,
Claw3D, ou un enfant ``caffeinate`` du superviseur. Cursor, ChatGPT, pytest et
le CLI ``jarvis stop`` lui-même sont exclus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import contextmanager
import errno
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import Any, Iterator, Mapping


LAUNCHD_LABELS: tuple[str, ...] = (
    "com.jarvis.supervisor",
    "com.jarvis.imessage-daemon",
    "com.jarvis.tv",
    "com.jarvis.tv-browser",
)

PROTECTED_MARKERS: tuple[str, ...] = (
    "Cursor Helper",
    "cursor-agent",
    "/Applications/Cursor.app",
    "ChatGPT.app",
    "cua_node",
)

SELF_CLI_MARKERS: tuple[str, ...] = (
    "scripts/jarvis_launchd.py",
    "scripts/jarvis_stack.py",
    "/scripts/jarvis ",
)

SUPERVISOR_LOCK_PATH = "/tmp/jarvis_supervisor.lock"
CLI_MUTEX_PATH = "/tmp/jarvis_cli.lock"


class RestartBlocked(RuntimeError):
    """Relance refusée : arrêt incomplet, verrou ou port tiers."""

SCRIPT_SERVICES: tuple[tuple[str, str], ...] = (
    ("scripts/imessage_daemon.py", "imessage_daemon"),
    ("scripts/audio_daemon.py", "audio_daemon"),
    ("scripts/jarvis_daemon.py", "jarvis_daemon"),
    ("scripts/jarvis_agent.py", "jarvis_agent"),
    ("scripts/screen_watcher.py", "screen_watcher"),
    ("native_audio/qwen3_local.py", "tts"),
    ("tv/server.py", "tv_dashboard"),
)


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    pid: int
    ppid: int
    command: str
    cwd: str | None = None


@dataclass(frozen=True, slots=True)
class OwnedProcess:
    pid: int
    ppid: int
    service: str
    command: str


@dataclass
class StopReport:
    ok: bool
    stopped: list[OwnedProcess] = field(default_factory=list)
    still_alive: list[int] = field(default_factory=list)
    bootout: list[str] = field(default_factory=list)
    managers: dict[str, object] = field(default_factory=dict)


def _norm(command: str) -> str:
    return command.replace("\\", "/")


def _is_protected(command: str) -> bool:
    text = _norm(command)
    if "pytest" in text:
        return True
    return any(marker in text for marker in PROTECTED_MARKERS)


def _is_self_cli(command: str) -> bool:
    text = _norm(command)
    return any(marker in text for marker in SELF_CLI_MARKERS)


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _cwd_under(cwd: str | None, directory: Path) -> bool:
    if not cwd:
        return False
    try:
        current = _resolved(cwd)
        target = _resolved(directory)
    except OSError:
        return False
    return current == target or target in current.parents


def _has_script(command: str, root: Path, relative: str) -> bool:
    text = _norm(command)
    absolute = _norm(str(root / relative))
    if absolute in text:
        return True
    token = f" {relative} "
    return token in f" {text} " or text.endswith(relative)


def classify_process(snapshot: ProcessSnapshot, root: Path) -> str | None:
    """Retourne l'identifiant de service si le processus appartient à JARVIS."""
    command = snapshot.command
    if _is_protected(command) or _is_self_cli(command):
        return None

    root = _resolved(root)
    text = _norm(command)
    cwd = snapshot.cwd

    if (
        text.strip() == "ollama serve"
        or text.endswith("ollama serve")
        or " ollama serve " in f" {text} "
    ):
        return "ollama"

    claw3d_root = root / ".jarvis" / "apps" / "claw3d"
    if "next-server" in text and (
        "claw3d" in text.lower() or _cwd_under(cwd, claw3d_root)
    ):
        return "claw3d"

    for relative, service in SCRIPT_SERVICES:
        if _has_script(text, root, relative):
            return service

    at_root = _cwd_under(cwd, root)
    if _has_script(text, root, "supervisor.py") and (
        at_root or _norm(str(root)) in text
    ):
        return "supervisor"

    if _has_script(text, root, "main.py") and (
        at_root or _norm(str(root / "main.py")) in text
    ):
        return "backend"

    node_like = "node " in f"{text} " or text.startswith("node") or "next-server" in text
    if node_like:
        for folder, service in (
            (root / "frontend", "frontend_dev"),
            (root / "web", "frontend_dev"),
            (root / "tv", "tv_dashboard"),
            (claw3d_root, "claw3d"),
        ):
            if _cwd_under(cwd, folder) or _norm(str(folder)) in text:
                return service

    return None


def select_owned(
    snapshots: Sequence[ProcessSnapshot],
    root: Path,
) -> list[OwnedProcess]:
    """Sélectionne les processus JARVIS, y compris caffeinate enfant du superviseur."""
    classified: list[OwnedProcess] = []
    for snapshot in snapshots:
        service = classify_process(snapshot, root)
        if service is None:
            continue
        classified.append(
            OwnedProcess(
                pid=snapshot.pid,
                ppid=snapshot.ppid,
                service=service,
                command=snapshot.command,
            )
        )
    owned_pids = {item.pid for item in classified}
    extras: list[OwnedProcess] = []
    for snapshot in snapshots:
        if snapshot.pid in owned_pids:
            continue
        if _is_protected(snapshot.command) or _is_self_cli(snapshot.command):
            continue
        if snapshot.command.strip() != "caffeinate -dims -t 0":
            continue
        if snapshot.ppid not in owned_pids:
            continue
        extras.append(
            OwnedProcess(
                pid=snapshot.pid,
                ppid=snapshot.ppid,
                service="caffeinate",
                command=snapshot.command,
            )
        )
    return classified + extras


def _parse_ps_line(line: str) -> ProcessSnapshot | None:
    stripped = line.strip()
    if not stripped:
        return None
    parts = stripped.split(None, 2)
    if len(parts) < 3:
        return None
    try:
        pid = int(parts[0])
        ppid = int(parts[1])
    except ValueError:
        return None
    return ProcessSnapshot(pid=pid, ppid=ppid, command=parts[2])


def _cwd_for_pid(pid: int) -> str | None:
    result = subprocess.run(
        ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in result.stdout.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def default_list_snapshots(root: Path) -> list[ProcessSnapshot]:
    result = subprocess.run(
        ["ps", "-axo", "pid=", "ppid=", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    snapshots: list[ProcessSnapshot] = []
    ambiguous: list[ProcessSnapshot] = []
    for line in result.stdout.splitlines():
        snapshot = _parse_ps_line(line)
        if snapshot is None:
            continue
        snapshots.append(snapshot)
        command = snapshot.command
        if any(
            marker in command
            for marker in (
                "supervisor.py",
                "main.py",
                "next-server",
                "caffeinate -dims",
            )
        ) or command.startswith("node"):
            ambiguous.append(snapshot)

    enriched: list[ProcessSnapshot] = []
    cwd_needed = {item.pid for item in ambiguous}
    cwd_cache: dict[int, str | None] = {}
    for snapshot in snapshots:
        cwd = cwd_cache.get(snapshot.pid) if snapshot.pid in cwd_needed else None
        if snapshot.pid in cwd_needed and snapshot.pid not in cwd_cache:
            cwd = _cwd_for_pid(snapshot.pid)
            cwd_cache[snapshot.pid] = cwd
        if cwd:
            snapshot = ProcessSnapshot(
                pid=snapshot.pid,
                ppid=snapshot.ppid,
                command=snapshot.command,
                cwd=cwd,
            )
        enriched.append(snapshot)
    return enriched


def default_bootout(*, uid: int | None = None) -> list[str]:
    user = os.getuid() if uid is None else uid
    unloaded: list[str] = []
    for label in LAUNCHD_LABELS:
        result = subprocess.run(
            ["launchctl", "bootout", f"gui/{user}/{label}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            unloaded.append(label)
    return unloaded


def default_stop_managers(root: Path) -> dict[str, object]:
    results: dict[str, object] = {}
    results["claw3d"] = _stop_claw3d(root)
    results["agentic_runtime"] = _stop_agentic_runtimes()
    results["ollama"] = _stop_ollama()
    return results


def _stop_claw3d(root: Path) -> dict[str, object]:
    try:
        from scripts import claw3d
    except Exception as exc:  # pragma: no cover - import fail-closed
        return {"ok": True, "skipped": True, "reason": str(exc)}
    if not claw3d.is_installed(root):
        return {"ok": True, "skipped": True, "reason": "not_installed"}
    try:
        claw3d.run_lifecycle(root, "stop.sh")
        return {"ok": True, "status": "stopped"}
    except (claw3d.Claw3DError, OSError, subprocess.CalledProcessError) as exc:
        return {"ok": False, "error": str(exc)}


def _stop_agentic_runtimes() -> dict[str, object]:
    """Arrête les runtimes agentiques découverts, sans nommer un fournisseur."""
    import importlib

    try:
        from jarvis.agentic.registry import discover_runtime_plugins

        manifests = discover_runtime_plugins()
    except Exception as exc:  # pragma: no cover
        return {"ok": True, "skipped": True, "reason": type(exc).__name__}
    if not manifests:
        return {"ok": True, "skipped": True, "reason": "no_plugin"}
    results: list[dict[str, object]] = []
    for manifest in manifests:
        module_name = f"integrations.{manifest.root.name}.scripts.manager"
        try:
            module = importlib.import_module(module_name)
            stop = getattr(module, "command_stop", None)
            if stop is None:
                results.append({"ok": True, "skipped": True, "reason": "no_stop"})
                continue
            results.append(dict(stop(None)))
        except Exception as exc:
            results.append({"ok": True, "skipped": True, "reason": type(exc).__name__})
    return {"ok": True, "results": results}


def _stop_ollama() -> dict[str, object]:
    try:
        from integrations.ollama_control import stop_ollama
    except Exception as exc:  # pragma: no cover
        return {"ok": True, "skipped": True, "reason": str(exc)}
    try:
        return dict(stop_ollama())
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def default_signal_pid(pid: int, sig: int) -> None:
    os.kill(pid, sig)


def default_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _ordered(owned: Sequence[OwnedProcess]) -> list[OwnedProcess]:
    pids = {item.pid for item in owned}
    children = [item for item in owned if item.ppid in pids]
    roots = [item for item in owned if item.ppid not in pids]
    return children + roots


def stop_stack(
    *,
    root: Path,
    snapshots: Sequence[ProcessSnapshot] | None = None,
    bootout: Callable[[], list[str]] | None = None,
    stop_managers: Callable[[], Mapping[str, object]] | None = None,
    list_snapshots: Callable[[], Sequence[ProcessSnapshot]] | None = None,
    signal_pid: Callable[[int, int], None] | None = None,
    pid_alive: Callable[[int], bool] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> StopReport:
    """Décharge launchd, arrête les managers, puis SIGTERM/SIGKILL les PID JARVIS."""
    import time as time_mod

    bootout_fn = bootout or default_bootout
    managers_fn = stop_managers or (lambda: default_stop_managers(root))
    signal_fn = signal_pid or default_signal_pid
    alive_fn = pid_alive or default_pid_alive
    sleep_fn = sleep or time_mod.sleep

    labels = list(bootout_fn())
    manager_results = dict(managers_fn())

    initial = snapshots
    if list_snapshots is not None:
        listing = list_snapshots
    elif initial is not None:
        listing = lambda: initial
    else:
        listing = lambda: default_list_snapshots(root)
    if initial is None:
        initial = listing()

    owned = select_owned(initial, root)
    ordered = _ordered(owned)
    for item in ordered:
        try:
            signal_fn(item.pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    sleep_fn(2.0)

    refreshed = select_owned(listing(), root)
    owned_now = {proc.pid for proc in refreshed}
    for item in ordered:
        if not alive_fn(item.pid):
            continue
        if item.pid not in owned_now:
            continue
        try:
            signal_fn(item.pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
    sleep_fn(0.5)

    still = [
        item.pid
        for item in ordered
        if alive_fn(item.pid) and item.pid in {proc.pid for proc in select_owned(listing(), root)}
    ]
    return StopReport(
        ok=not still,
        stopped=list(owned),
        still_alive=still,
        bootout=labels,
        managers=manager_results,
    )


def _config_float(name: str, default: float) -> float:
    try:
        import config as jarvis_config
    except ImportError:
        return default
    try:
        value = float(getattr(jarvis_config, name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def wait_predicate(
    predicate: Callable[[], bool],
    *,
    timeout_s: float,
    interval_s: float = 0.1,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> bool:
    """Attend un prédicat avec délai borné. Jamais de boucle infinie."""
    sleep_fn = sleep or time.sleep
    clock_fn = clock or time.monotonic
    deadline = clock_fn() + max(0.0, float(timeout_s))
    while True:
        if predicate():
            return True
        if clock_fn() >= deadline:
            return False
        sleep_fn(max(0.01, float(interval_s)))


def default_lock_held(path: str) -> bool:
    """True si un processus détient encore le flock. Fichier orphelin = libre."""
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


@contextmanager
def acquire_cli_mutex(
    path: str | None = None,
    *,
    timeout_s: float = 60.0,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> Iterator[None]:
    """Sérialise ``jarvis restart`` / ``jarvis maj``. Timeout borné."""
    lock_path = path or CLI_MUTEX_PATH
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        pass
    acquired = False

    def _try() -> bool:
        nonlocal acquired
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            return True
        except OSError as exc:
            if exc.errno in {errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK}:
                return False
            raise

    try:
        if not wait_predicate(
            _try, timeout_s=timeout_s, sleep=sleep, clock=clock
        ):
            raise RestartBlocked("une autre commande jarvis maj est en cours")
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def default_list_listeners(port: int) -> tuple[int, ...]:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        token = line.strip()
        if token.isdigit():
            pid = int(token)
            if pid > 0:
                pids.append(pid)
    return tuple(dict.fromkeys(pids))


def classify_port_occupancy(
    port: int,
    *,
    root: Path,
    list_listeners: Callable[[int], Sequence[int]],
    list_snapshots: Callable[[], Sequence[ProcessSnapshot]],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Sépare les PID JARVIS des occupants tiers. Un PID inconnu est tiers."""
    pids = tuple(dict.fromkeys(int(pid) for pid in list_listeners(port) if int(pid) > 0))
    if not pids:
        return (), ()
    owned_ids = {item.pid for item in select_owned(list_snapshots(), root)}
    owned = tuple(pid for pid in pids if pid in owned_ids)
    foreign = tuple(pid for pid in pids if pid not in owned_ids)
    return owned, foreign


def wait_after_stop(
    *,
    root: Path,
    ports: Sequence[int] = (9000,),
    lock_path: str = SUPERVISOR_LOCK_PATH,
    lock_timeout_s: float | None = None,
    port_timeout_s: float | None = None,
    lock_held: Callable[[str], bool] | None = None,
    list_listeners: Callable[[int], Sequence[int]] | None = None,
    list_snapshots: Callable[[], Sequence[ProcessSnapshot]] | None = None,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> None:
    """Après stop : verrou libre, ports JARVIS libres, occupant tiers = refus."""
    lock_timeout = (
        lock_timeout_s
        if lock_timeout_s is not None
        else _config_float("SUPERVISOR_RESTART_LOCK_TIMEOUT_S", 20.0)
    )
    port_timeout = (
        port_timeout_s
        if port_timeout_s is not None
        else _config_float("SUPERVISOR_RESTART_PORT_TIMEOUT_S", 15.0)
    )
    held_fn = lock_held or default_lock_held
    listeners_fn = list_listeners or default_list_listeners
    snapshots_fn = list_snapshots or (lambda: default_list_snapshots(root))
    if not wait_predicate(
        lambda: not held_fn(lock_path),
        timeout_s=lock_timeout,
        sleep=sleep,
        clock=clock,
    ):
        raise RestartBlocked("verrou superviseur encore tenu")

    sleep_fn = sleep or time.sleep
    clock_fn = clock or time.monotonic
    deadline = clock_fn() + port_timeout
    while True:
        foreign_found = False
        owned_found = False
        for port in ports:
            owned, foreign = classify_port_occupancy(
                int(port),
                root=root,
                list_listeners=listeners_fn,
                list_snapshots=snapshots_fn,
            )
            if foreign:
                foreign_found = True
                break
            if owned:
                owned_found = True
        if foreign_found:
            raise RestartBlocked("port occupé par un processus tiers")
        if not owned_found:
            return
        if clock_fn() >= deadline:
            raise RestartBlocked("ports JARVIS encore occupés")
        sleep_fn(0.1)


def prepare_supervisor_bind(
    root: Path,
    port: int,
    **kwargs: Any,
) -> None:
    """Avant bind : attendre les PID JARVIS, refuser un occupant tiers.

    Ne signale jamais un processus. Un port occupé ne prouve pas la propriété.
    """
    wait_after_stop(root=root, ports=(int(port),), **kwargs)


def cli_restart(
    *,
    root: Path,
    stop: Callable[[], int | StopReport],
    start: Callable[[], int],
    wait: Callable[..., None] | None = None,
    mutex: Callable[..., Any] | None = None,
    ports: Sequence[int] = (9000,),
) -> int:
    """Stop → attente bornée → start. Idempotent si déjà arrêté. Fail-closed."""
    mutex_cm = mutex or acquire_cli_mutex
    waiter = wait or wait_after_stop
    with mutex_cm():
        result = stop()
        if isinstance(result, StopReport):
            if not result.ok:
                raise RestartBlocked("arrêt incomplet")
        elif int(result) != 0:
            raise RestartBlocked("arrêt incomplet")
        waiter(root=root, ports=tuple(ports))
        return int(start())

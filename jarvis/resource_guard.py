"""Garde-fou RAM / process — périmètre JARVIS uniquement (politique A).

Inventorie les process rattachés au dépôt, planifie des actions bornées
(orphelins TTS, doublons, stop Ollama idle), et exécute via des callbacks
injectables. Aucun kill hors allowlist de marqueurs.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Kinds killables / gérés. Tout le reste est ignoré.
JARVIS_KINDS = frozenset(
    {
        "tts_sidecar",
        "audio_daemon",
        "jarvis_daemon",
        "screen_watcher",
        "ollama_serve",
        "ollama_runner",
    }
)

_DAEMON_SCRIPTS: dict[str, str] = {
    "audio_daemon": "scripts/audio_daemon.py",
    "jarvis_daemon": "scripts/jarvis_daemon.py",
    "screen_watcher": "scripts/screen_watcher.py",
}

_TTS_REL = "native_audio/qwen3_local.py"

_PAGE_SIZE_RE = re.compile(r"page size of (\d+)", re.I)
_PAGES_FREE_RE = re.compile(r"Pages free:\s*(\d+)", re.I)
_PAGES_PURGE_RE = re.compile(r"Pages purgeable:\s*(\d+)", re.I)

# Refuse explicitement ces motifs même s'ils coïncident par accident.
_FORBIDDEN_SUBSTRINGS = (
    "codex",
    "claude.app",
    "claudefordesktop",
    "cursor.app",
    "google chrome",
    "chromium",
)


@dataclass(frozen=True)
class GuardConfig:
    enabled: bool
    warn_free_mb: int
    critical_free_mb: int
    ollama_idle_stop: bool
    ollama_idle_ttl_s: float
    tts_max_workers: int
    kill_orphans: bool
    dry_run: bool
    project_dir: Path


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    rss_kb: int
    cmdline: str
    kind: str


@dataclass(frozen=True)
class MemorySnapshot:
    page_size: int
    pages_free: int
    pages_purgeable: int

    @property
    def free_mb(self) -> float:
        pages = self.pages_free + self.pages_purgeable
        return (pages * self.page_size) / (1024.0 * 1024.0)


@dataclass(frozen=True)
class GuardAction:
    action: str
    pid: int | None
    reason: str
    executed: bool
    dry_run: bool


@dataclass
class GuardReport:
    level: str
    free_mb: float | None
    processes: list[ProcessInfo]
    actions: list[GuardAction]
    screen_watcher_running: bool
    ollama_idle_seconds: float | None
    ts: float = field(default_factory=time.time)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "free_mb": None if self.free_mb is None else round(self.free_mb, 1),
            "screen_watcher_running": self.screen_watcher_running,
            "ollama_idle_seconds": (
                None
                if self.ollama_idle_seconds is None
                else round(self.ollama_idle_seconds, 1)
            ),
            "processes": [
                {
                    "pid": p.pid,
                    "ppid": p.ppid,
                    "rss_mb": round(p.rss_kb / 1024.0, 1),
                    "kind": p.kind,
                    "cmdline": p.cmdline[:200],
                }
                for p in self.processes
            ],
            "actions": [
                {
                    "action": a.action,
                    "pid": a.pid,
                    "reason": a.reason,
                    "executed": a.executed,
                    "dry_run": a.dry_run,
                }
                for a in self.actions
            ],
            "ts": self.ts,
        }


def config_from_settings(settings: Any, *, project_dir: Path) -> GuardConfig:
    """Construit la config depuis le module ``config`` (ou un namespace de test)."""

    def _bool(name: str, default: bool) -> bool:
        raw = getattr(settings, name, default)
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def _int(name: str, default: int) -> int:
        return int(getattr(settings, name, default))

    def _float(name: str, default: float) -> float:
        return float(getattr(settings, name, default))

    return GuardConfig(
        enabled=_bool("RESOURCE_GUARD_ENABLED", True),
        warn_free_mb=_int("RESOURCE_GUARD_WARN_FREE_MB", 2048),
        critical_free_mb=_int("RESOURCE_GUARD_CRITICAL_FREE_MB", 1024),
        ollama_idle_stop=_bool("RESOURCE_GUARD_OLLAMA_IDLE_STOP", True),
        ollama_idle_ttl_s=_float("RESOURCE_GUARD_OLLAMA_IDLE_TTL_S", 120.0),
        tts_max_workers=max(1, _int("RESOURCE_GUARD_TTS_MAX_WORKERS", 1)),
        kill_orphans=_bool("RESOURCE_GUARD_KILL_ORPHANS", True),
        dry_run=_bool("RESOURCE_GUARD_DRY_RUN", False),
        project_dir=Path(project_dir).resolve(),
    )


def classify_cmdline(cmdline: str, project_dir: Path) -> str | None:
    """Retourne un kind JARVIS ou ``None`` si hors périmètre (ne jamais tuer)."""
    text = (cmdline or "").strip()
    if not text:
        return None
    lower = text.lower()
    if any(bad in lower for bad in _FORBIDDEN_SUBSTRINGS):
        return None

    root = str(Path(project_dir).resolve())
    tts_marker = str(Path(project_dir).resolve() / _TTS_REL)
    if tts_marker in text:
        return "tts_sidecar"

    for kind, rel in _DAEMON_SCRIPTS.items():
        marker = str(Path(project_dir).resolve() / rel)
        if marker in text:
            return kind

    # Ollama : pas de chemin dépôt, mais contrat explicite.
    if "ollama serve" in lower or lower.rstrip().endswith("ollama serve"):
        return "ollama_serve"
    if "llama-server" in lower and (".ollama" in lower or "ollama" in lower):
        return "ollama_runner"
    if "ollama" in lower and "runner" in lower:
        return "ollama_runner"

    # Filet : chemin dépôt + script connu sans match exact (symlinks rares).
    if root in text and "qwen3_local.py" in text:
        return "tts_sidecar"

    return None


def parse_memory_pressure(text: str) -> MemorySnapshot:
    """Parse la sortie textuelle de ``memory_pressure`` (macOS)."""
    page_size = 16384
    pages_free = 0
    pages_purge = 0
    m = _PAGE_SIZE_RE.search(text or "")
    if m:
        page_size = int(m.group(1))
    m = _PAGES_FREE_RE.search(text or "")
    if m:
        pages_free = int(m.group(1))
    m = _PAGES_PURGE_RE.search(text or "")
    if m:
        pages_purge = int(m.group(1))
    return MemorySnapshot(
        page_size=page_size,
        pages_free=pages_free,
        pages_purgeable=pages_purge,
    )


def read_memory_free_mb() -> float | None:
    """Lit la RAM libre+purgeable via ``memory_pressure`` ; ``None`` si indisponible."""
    try:
        proc = subprocess.run(
            ["memory_pressure"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("[resource_guard] memory_pressure indisponible : %s", exc)
        return None
    snap = parse_memory_pressure(proc.stdout or proc.stderr or "")
    if snap.pages_free == 0 and snap.pages_purgeable == 0 and "Pages free" not in (
        proc.stdout or ""
    ):
        return None
    return snap.free_mb


def list_jarvis_processes(project_dir: Path) -> list[ProcessInfo]:
    """Inventaire macOS/Linux via ``ps`` — filtre allowlist uniquement."""
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,rss=,command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("[resource_guard] ps échoué : %s", exc)
        return []

    out: list[ProcessInfo] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            rss_kb = int(parts[2])
        except ValueError:
            continue
        cmdline = parts[3]
        kind = classify_cmdline(cmdline, project_dir)
        if kind is None:
            continue
        out.append(
            ProcessInfo(
                pid=pid,
                ppid=ppid,
                rss_kb=rss_kb,
                cmdline=cmdline,
                kind=kind,
            )
        )
    return out


def memory_level(free_mb: float | None, config: GuardConfig) -> str:
    if free_mb is None:
        return "ok"
    if free_mb < config.critical_free_mb:
        return "critical"
    if free_mb < config.warn_free_mb:
        return "warn"
    return "ok"


def plan_actions(
    processes: list[ProcessInfo],
    managed_pids: set[int],
    free_mb: float | None,
    config: GuardConfig,
    *,
    screen_watcher_running: bool,
    ollama_idle_s: float,
) -> list[GuardAction]:
    """Décide des actions — pure, sans effet de bord."""
    if not config.enabled:
        return []

    actions: list[GuardAction] = []
    level = memory_level(free_mb, config)

    if config.kill_orphans:
        actions.extend(_plan_tts_actions(processes, managed_pids, config))
        actions.extend(_plan_daemon_duplicates(processes, managed_pids, config))

    if (
        config.ollama_idle_stop
        and not screen_watcher_running
        and ollama_idle_s >= config.ollama_idle_ttl_s
        and any(p.kind in {"ollama_serve", "ollama_runner"} for p in processes)
    ):
        # Stop Ollama dès TTL atteint quand SW est off ; en critical on
        # journalise plus fort mais la condition est la même (idle + SW off).
        reason = (
            f"screen_watcher arrêté depuis {ollama_idle_s:.0f}s "
            f"(ttl={config.ollama_idle_ttl_s:.0f}s, level={level})"
        )
        actions.append(
            GuardAction(
                action="stop_ollama_idle",
                pid=None,
                reason=reason,
                executed=False,
                dry_run=config.dry_run,
            )
        )

    return actions


def _plan_tts_actions(
    processes: list[ProcessInfo],
    managed_pids: set[int],
    config: GuardConfig,
) -> list[GuardAction]:
    actions: list[GuardAction] = []
    tts = [p for p in processes if p.kind == "tts_sidecar"]
    orphans = [p for p in tts if p.ppid not in managed_pids and p.pid not in managed_pids]
    for p in orphans:
        actions.append(
            GuardAction(
                action="kill_orphan_tts",
                pid=p.pid,
                reason=f"sidecar TTS orphelin (ppid={p.ppid})",
                executed=False,
                dry_run=config.dry_run,
            )
        )

    attached = sorted(
        (p for p in tts if p.ppid in managed_pids or p.pid in managed_pids),
        key=lambda p: p.pid,
    )
    # Aussi compter les orphelins déjà planifiés comme « en trop » une fois
    # tués ; pour le cap, on regarde les attachés gérés.
    if len(attached) > config.tts_max_workers:
        for p in attached[config.tts_max_workers :]:
            actions.append(
                GuardAction(
                    action="kill_duplicate_tts",
                    pid=p.pid,
                    reason=f"cap TTS={config.tts_max_workers}, surplus pid={p.pid}",
                    executed=False,
                    dry_run=config.dry_run,
                )
            )

    # Si aucun attaché mais plusieurs orphelins : le premier plan orphelin
    # les tue tous ; pas de double action.
    return actions


def _plan_daemon_duplicates(
    processes: list[ProcessInfo],
    managed_pids: set[int],
    config: GuardConfig,
) -> list[GuardAction]:
    actions: list[GuardAction] = []
    for kind in ("audio_daemon", "jarvis_daemon", "screen_watcher"):
        group = [p for p in processes if p.kind == kind]
        if len(group) <= 1:
            continue
        kept = [p for p in group if p.ppid in managed_pids or p.pid in managed_pids]
        if not kept:
            # Aucun rattaché : garder le PID le plus bas, tuer le reste.
            kept = [min(group, key=lambda p: p.pid)]
        kept_pids = {p.pid for p in kept}
        for p in group:
            if p.pid in kept_pids:
                continue
            if p.ppid in managed_pids or p.pid in managed_pids:
                # Plusieurs gérés : garder le plus bas PID géré.
                if p.pid != min(kept_pids):
                    actions.append(
                        GuardAction(
                            action="kill_duplicate_daemon",
                            pid=p.pid,
                            reason=f"doublon {kind} (gardé={min(kept_pids)})",
                            executed=False,
                            dry_run=config.dry_run,
                        )
                    )
                continue
            actions.append(
                GuardAction(
                    action="kill_duplicate_daemon",
                    pid=p.pid,
                    reason=f"doublon {kind} hors arbre géré",
                    executed=False,
                    dry_run=config.dry_run,
                )
            )
    return actions


class ResourceGuard:
    """État + tick périodique du garde-fou."""

    def __init__(
        self,
        config: GuardConfig,
        *,
        list_processes: Callable[[], list[ProcessInfo]] | None = None,
        read_free_mb: Callable[[], float | None] | None = None,
        is_screen_watcher_running: Callable[[], bool],
        managed_pids: Callable[[], set[int]],
        kill_process_tree: Callable[..., None],
        stop_ollama: Callable[[], dict[str, Any]],
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self._list_processes = list_processes or (
            lambda: list_jarvis_processes(config.project_dir)
        )
        self._read_free_mb = read_free_mb or read_memory_free_mb
        self._is_sw_running = is_screen_watcher_running
        self._managed_pids = managed_pids
        self._kill_tree = kill_process_tree
        self._stop_ollama = stop_ollama
        self._monotonic = monotonic or time.monotonic
        self._sw_stopped_since: float | None = None
        self._last_report: GuardReport | None = None
        self._last_tick_at: float = 0.0

    @property
    def last_report(self) -> GuardReport | None:
        return self._last_report

    def should_tick(self, interval_s: float) -> bool:
        return (self._monotonic() - self._last_tick_at) >= interval_s

    def tick(self) -> GuardReport:
        self._last_tick_at = self._monotonic()
        if not self.config.enabled:
            report = GuardReport(
                level="ok",
                free_mb=None,
                processes=[],
                actions=[],
                screen_watcher_running=False,
                ollama_idle_seconds=None,
            )
            self._last_report = report
            return report

        sw_running = bool(self._is_sw_running())
        now = self._monotonic()
        if sw_running:
            self._sw_stopped_since = None
            idle_s = 0.0
        else:
            if self._sw_stopped_since is None:
                self._sw_stopped_since = now
            idle_s = max(0.0, now - self._sw_stopped_since)

        processes = self._list_processes()
        free_mb = self._read_free_mb()
        level = memory_level(free_mb, self.config)
        planned = plan_actions(
            processes,
            self._managed_pids(),
            free_mb,
            self.config,
            screen_watcher_running=sw_running,
            ollama_idle_s=idle_s,
        )
        executed = [self._execute(a) for a in planned]
        report = GuardReport(
            level=level,
            free_mb=free_mb,
            processes=processes,
            actions=executed,
            screen_watcher_running=sw_running,
            ollama_idle_seconds=idle_s if not sw_running else 0.0,
        )
        self._last_report = report
        if executed:
            logger.warning(
                "[resource_guard] level=%s free_mb=%s actions=%s",
                level,
                None if free_mb is None else round(free_mb, 1),
                [(a.action, a.pid, a.executed, a.dry_run) for a in executed],
            )
        elif level != "ok":
            logger.info(
                "[resource_guard] level=%s free_mb=%s jarvis_procs=%d",
                level,
                None if free_mb is None else round(free_mb, 1),
                len(processes),
            )
        return report

    def _execute(self, action: GuardAction) -> GuardAction:
        if action.dry_run or self.config.dry_run:
            logger.info(
                "[resource_guard] DRY_RUN %s pid=%s — %s",
                action.action,
                action.pid,
                action.reason,
            )
            return GuardAction(
                action=action.action,
                pid=action.pid,
                reason=action.reason,
                executed=False,
                dry_run=True,
            )

        try:
            if action.action in {
                "kill_orphan_tts",
                "kill_duplicate_tts",
                "kill_duplicate_daemon",
            }:
                if action.pid is None:
                    return action
                # Double-check allowlist juste avant le signal.
                if not self._pid_still_jarvis(action.pid):
                    logger.warning(
                        "[resource_guard] refuse kill pid=%s — plus JARVIS",
                        action.pid,
                    )
                    return GuardAction(
                        action=action.action,
                        pid=action.pid,
                        reason=action.reason + " (refusé: cmdline hors allowlist)",
                        executed=False,
                        dry_run=False,
                    )
                logger.warning(
                    "[resource_guard] SIGTERM %s pid=%s — %s",
                    action.action,
                    action.pid,
                    action.reason,
                )
                self._kill_tree(action.pid, sig=signal.SIGTERM)
                time.sleep(0.4)
                if _pid_alive(action.pid):
                    logger.warning(
                        "[resource_guard] SIGKILL %s pid=%s",
                        action.action,
                        action.pid,
                    )
                    self._kill_tree(action.pid, sig=signal.SIGKILL)
                return GuardAction(
                    action=action.action,
                    pid=action.pid,
                    reason=action.reason,
                    executed=True,
                    dry_run=False,
                )

            if action.action == "stop_ollama_idle":
                logger.warning("[resource_guard] stop_ollama — %s", action.reason)
                self._stop_ollama()
                return GuardAction(
                    action=action.action,
                    pid=None,
                    reason=action.reason,
                    executed=True,
                    dry_run=False,
                )
        except Exception as exc:
            logger.error(
                "[resource_guard] échec %s pid=%s : %s",
                action.action,
                action.pid,
                exc,
            )
            return GuardAction(
                action=action.action,
                pid=action.pid,
                reason=f"{action.reason} (erreur: {exc})",
                executed=False,
                dry_run=False,
            )
        return action

    def _pid_still_jarvis(self, pid: int) -> bool:
        try:
            ps = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        cmdline = (ps.stdout or "").strip()
        kind = classify_cmdline(cmdline, self.config.project_dir)
        return kind in JARVIS_KINDS and kind not in {"ollama_serve", "ollama_runner"}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

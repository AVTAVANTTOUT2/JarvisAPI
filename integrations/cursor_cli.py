"""Détection et inspection de la CLI Cursor Agent — sans supposer la syntaxe."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CursorCliInfo:
    available: bool
    path: str | None = None
    version: str | None = None
    authenticated: bool | None = None
    supports_print: bool = False
    supports_force: bool = False
    supports_workspace: bool = False
    supports_worktree: bool = False
    supports_trust: bool = False
    help_text: str = ""
    error: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "path": self.path,
            "version": self.version,
            "authenticated": self.authenticated,
            "supports_print": self.supports_print,
            "supports_force": self.supports_force,
            "supports_workspace": self.supports_workspace,
            "supports_worktree": self.supports_worktree,
            "supports_trust": self.supports_trust,
            "error": self.error,
            **self.extras,
        }


def _run(cmd: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "NO_OPEN_BROWSER": "1"},
    )


def resolve_cursor_agent_path(configured: str | None = None) -> str | None:
    """Résout le binaire agent Cursor (préféré) ou cursor-agent."""
    if configured and configured.strip():
        p = Path(configured.strip()).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
        which_cfg = shutil.which(configured.strip())
        if which_cfg:
            return which_cfg
    for name in ("agent", "cursor-agent"):
        found = shutil.which(name)
        if found:
            return found
    # Fallback : `cursor agent` sous-commande — on utilisera le wrapper shell
    cursor = shutil.which("cursor")
    if cursor:
        return cursor
    return None


def inspect_cursor_cli(configured_path: str | None = None) -> CursorCliInfo:
    """Exécute which / --help / --version / status pour découvrir les capacités."""
    path = resolve_cursor_agent_path(configured_path)
    if not path:
        return CursorCliInfo(available=False, error="CLI Cursor Agent introuvable (agent / cursor-agent)")

    info = CursorCliInfo(available=True, path=path)
    basename = Path(path).name

    # Version
    try:
        ver = _run([path, "--version"] if basename != "cursor" else [path, "agent", "--version"])
        info.version = (ver.stdout or ver.stderr or "").strip().splitlines()[0] if ver.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired) as exc:
        info.error = f"version: {exc}"

    # Help
    try:
        help_cmd = [path, "--help"] if basename != "cursor" else [path, "agent", "--help"]
        help_p = _run(help_cmd, timeout=20.0)
        help_text = (help_p.stdout or "") + (help_p.stderr or "")
        info.help_text = help_text
        low = help_text.lower()
        info.supports_print = "--print" in low or "-p" in help_text
        info.supports_force = "--force" in low or "--yolo" in low
        info.supports_workspace = "--workspace" in low
        info.supports_worktree = "--worktree" in low
        info.supports_trust = "--trust" in low
    except (OSError, subprocess.TimeoutExpired) as exc:
        info.error = f"help: {exc}"

    # Auth status
    try:
        status_cmd = [path, "status"] if basename != "cursor" else [path, "agent", "status"]
        st = _run(status_cmd, timeout=20.0)
        out = ((st.stdout or "") + (st.stderr or "")).lower()
        if st.returncode == 0 and ("logged in" in out or "✓" in (st.stdout or "")):
            info.authenticated = True
        elif "not logged" in out or "login" in out and "logged in" not in out:
            info.authenticated = False
        else:
            info.authenticated = st.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        info.authenticated = None

    return info


def build_agent_command(
    info: CursorCliInfo,
    prompt: str,
    *,
    workspace: str,
    force: bool = True,
    trust: bool = True,
) -> list[str]:
    """Construit la commande agent headless selon les flags réellement supportés.

    Refuse de construire une commande interactive : sans ``--print`` le CLI
    attendrait une entrée utilisateur et pendrait jusqu'au timeout.
    """
    if not info.path:
        raise RuntimeError("Cursor CLI path manquant")
    if not info.supports_print:
        raise RuntimeError(
            "Cursor CLI sans mode headless (--print) — délégation refusée "
            f"(version détectée: {info.version or 'inconnue'})"
        )
    basename = Path(info.path).name
    if basename == "cursor":
        cmd = [info.path, "agent"]
    else:
        cmd = [info.path]

    cmd.extend(["--print", "--output-format", "text"])
    if force and info.supports_force:
        cmd.append("--force")
    if trust and info.supports_trust:
        cmd.append("--trust")
    if info.supports_workspace:
        cmd.extend(["--workspace", workspace])
    cmd.append(prompt)
    return cmd

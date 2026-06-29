"""Helper unifié pour exécuter de l'AppleScript via `osascript`.

Centralise la logique subprocess + gestion d'erreurs partagée par tous les
modules d'intégration macOS (Mail, Calendar, iMessage, Contacts, Computer,
Notifications). Avant ce module, le pattern `subprocess.run(["osascript", "-e", …])`
était dupliqué dans 6 fichiers avec des variantes d'erreur subtiles.

Usage :

    from integrations._applescript import run_applescript, run_applescript_async

    result = run_applescript('tell application "Mail" to get name', timeout=10)
    if result.ok:
        print(result.stdout)
    else:
        print(f"Erreur ({result.reason}): {result.stderr}")
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

OsascriptReason = Literal[
    "ok", "timeout", "nonzero_exit", "not_found", "not_macos", "exception"
]


@dataclass
class OsascriptResult:
    """Diagnostic complet d'une exécution AppleScript."""
    ok: bool
    reason: OsascriptReason
    returncode: int | None
    stdout: str
    stderr: str

    def is_permission_denied(self) -> bool:
        """True si l'erreur vient d'un refus d'Automation macOS."""
        err = (self.stderr or "").lower()
        return (
            "not authorized to send apple events" in err
            or "execution error: -1743" in err
            or "execution error: -10004" in err
        )

    def is_app_not_running(self) -> bool:
        """True si l'app cible est éteinte (erreur -600 typiquement)."""
        err = (self.stderr or "").lower()
        return "-600" in err or "not open" in err or "not running" in err


def is_macos_with_osascript() -> bool:
    """True si on tourne sur macOS avec osascript disponible."""
    return platform.system() == "Darwin" and shutil.which("osascript") is not None


def run_applescript(
    script: str,
    *,
    timeout: float = 30.0,
    extra_env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> OsascriptResult:
    """Exécute un AppleScript en bloquant. Retourne `OsascriptResult`.

    Ne logge pas les erreurs lui-même (le caller décide du niveau de log selon
    le contexte). Pour les modules qui ont besoin de retry/cooldown spécifique,
    utiliser ce helper comme primitive bas-niveau.
    """
    if not is_macos_with_osascript():
        return OsascriptResult(
            ok=False,
            reason="not_macos",
            returncode=None,
            stdout="",
            stderr="osascript introuvable (macOS requis).",
        )

    env = None
    if extra_env:
        env = {**os.environ, **extra_env}

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return OsascriptResult(
            ok=False,
            reason="timeout",
            returncode=None,
            stdout="",
            stderr=f"osascript timeout ({timeout:.1f}s)",
        )
    except FileNotFoundError:
        return OsascriptResult(
            ok=False,
            reason="not_found",
            returncode=None,
            stdout="",
            stderr="osascript introuvable",
        )
    except Exception as e:  # noqa: BLE001 — on capture tout pour ne pas crasher l'appelant
        return OsascriptResult(
            ok=False,
            reason="exception",
            returncode=None,
            stdout="",
            stderr=f"{type(e).__name__}: {e}",
        )

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    return OsascriptResult(
        ok=result.returncode == 0,
        reason="ok" if result.returncode == 0 else "nonzero_exit",
        returncode=result.returncode,
        stdout=stdout,
        stderr=stderr,
    )


async def run_applescript_async(
    script: str,
    *,
    timeout: float = 30.0,
    extra_env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> OsascriptResult:
    """Exécute un AppleScript de manière non-bloquante via asyncio."""
    if not is_macos_with_osascript():
        return OsascriptResult(
            ok=False,
            reason="not_macos",
            returncode=None,
            stdout="",
            stderr="osascript introuvable (macOS requis).",
        )

    env = None
    if extra_env:
        env = {**os.environ, **extra_env}

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        return OsascriptResult(
            ok=False,
            reason="timeout",
            returncode=None,
            stdout="",
            stderr=f"osascript timeout ({timeout:.1f}s)",
        )
    except FileNotFoundError:
        return OsascriptResult(
            ok=False,
            reason="not_found",
            returncode=None,
            stdout="",
            stderr="osascript introuvable",
        )
    except Exception as e:  # noqa: BLE001
        return OsascriptResult(
            ok=False,
            reason="exception",
            returncode=None,
            stdout="",
            stderr=f"{type(e).__name__}: {e}",
        )

    stdout = (stdout_b.decode("utf-8", errors="replace") or "").strip()
    stderr = (stderr_b.decode("utf-8", errors="replace") or "").strip()
    return OsascriptResult(
        ok=proc.returncode == 0,
        reason="ok" if proc.returncode == 0 else "nonzero_exit",
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def escape_applescript_string(text: str) -> str:
    """Échappe une chaîne pour l'insertion dans un AppleScript entre guillemets.

    Ordre critique : antislash AVANT guillemet, sinon double-escape.
    """
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )

"""Pont macOS vers l'app Raccourcis (Shortcuts.app) via le CLI ``shortcuts``.

Sécurité :
- opt-in explicite (``APPLE_SHORTCUTS_ENABLED``) ;
- le LLM ne peut lancer que des raccourcis présents dans le registre SQLite ;
- toute exécution passe par un plan opaque à usage unique + confirmation ;
- l'entrée éventuelle est écrite dans un workspace dédié (jamais un chemin
  fourni par le modèle).

Le CLI ``/usr/bin/shortcuts`` (Monterey+) liste et exécute les raccourcis
installés. AppleScript n'est pas utilisé ici : le dictionnaire Shortcuts.app
est moins stable que le CLI officiel.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import secrets
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import config

logger = logging.getLogger(__name__)

SHORTCUTS_BIN_CANDIDATES: tuple[str, ...] = (
    "/usr/bin/shortcuts",
    "/usr/local/bin/shortcuts",
)

MAX_SHORTCUT_NAME_LEN = 120
MAX_INPUT_CHARS = 8_000
MAX_OUTPUT_CHARS = 12_000
MAX_PENDING_PLANS = 32
DEFAULT_RUN_TIMEOUT = 60.0

PlanStatus = Literal["pending", "consumed", "expired", "revoked"]


class AppleShortcutsError(Exception):
    """Erreur métier du pont Raccourcis."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ShortcutPlan:
    """Plan opaque figé avant confirmation humaine."""

    plan_id: str
    shortcut_name: str
    registry_id: int
    input_text: str | None
    allow_input: bool
    risk: str
    created_at: float
    expires_at: float

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "shortcut_name": self.shortcut_name,
            "registry_id": self.registry_id,
            "has_input": bool(self.input_text),
            "input_preview": _preview(self.input_text),
            "allow_input": self.allow_input,
            "risk": self.risk,
            "expires_at": self.expires_at,
        }


_pending_plans: dict[str, ShortcutPlan] = {}
_plans_lock = threading.Lock()


def _preview(text: str | None, *, limit: int = 80) -> str | None:
    if text is None:
        return None
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def is_macos() -> bool:
    return platform.system() == "Darwin"


def resolve_shortcuts_bin() -> str | None:
    """Retourne le chemin absolu du CLI ``shortcuts``, ou ``None``."""
    configured = str(getattr(config, "APPLE_SHORTCUTS_BIN", "") or "").strip()
    candidates = ((configured,) if configured else ()) + SHORTCUTS_BIN_CANDIDATES
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    which = shutil.which("shortcuts")
    return which


def is_enabled() -> bool:
    return bool(getattr(config, "APPLE_SHORTCUTS_ENABLED", False))


def workspace_dir() -> Path:
    raw = getattr(
        config,
        "APPLE_SHORTCUTS_WORKSPACE",
        str(Path(config.BASE_DIR) / "data" / "apple_shortcuts_workspace"),
    )
    path = Path(str(raw)).expanduser().resolve()
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def status() -> dict[str, Any]:
    """Diagnostic sans exécuter de raccourci."""
    bin_path = resolve_shortcuts_bin()
    return {
        "enabled": is_enabled(),
        "macos": is_macos(),
        "cli_available": bin_path is not None,
        "cli_path": bin_path,
        "workspace": str(workspace_dir()) if is_macos() else None,
        "plan_ttl_seconds": int(
            getattr(config, "APPLE_SHORTCUTS_PLAN_TTL_SECONDS", 600)
        ),
        "run_timeout_seconds": float(
            getattr(config, "APPLE_SHORTCUTS_RUN_TIMEOUT", DEFAULT_RUN_TIMEOUT)
        ),
        "available": is_enabled() and is_macos() and bin_path is not None,
    }


def _validate_shortcut_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise AppleShortcutsError("invalid_name", "Nom de raccourci manquant.")
    if len(cleaned) > MAX_SHORTCUT_NAME_LEN:
        raise AppleShortcutsError(
            "invalid_name",
            f"Nom de raccourci trop long (max {MAX_SHORTCUT_NAME_LEN}).",
        )
    if "\x00" in cleaned or "\n" in cleaned or "\r" in cleaned:
        raise AppleShortcutsError("invalid_name", "Nom de raccourci invalide.")
    return cleaned


def _validate_input(text: str | None, *, allow_input: bool) -> str | None:
    if text is None:
        return None
    if not allow_input:
        raise AppleShortcutsError(
            "input_forbidden",
            "Ce raccourci n'accepte pas d'entrée texte.",
        )
    cleaned = str(text)
    if len(cleaned) > MAX_INPUT_CHARS:
        raise AppleShortcutsError(
            "input_too_long",
            f"Entrée trop longue (max {MAX_INPUT_CHARS} caractères).",
        )
    if "\x00" in cleaned:
        raise AppleShortcutsError("invalid_input", "Entrée invalide.")
    return cleaned


def _run_cli(
    args: list[str],
    *,
    timeout: float,
    input_bytes: bytes | None = None,
) -> tuple[int, str, str]:
    bin_path = resolve_shortcuts_bin()
    if not bin_path:
        raise AppleShortcutsError(
            "cli_missing",
            "Le CLI shortcuts est introuvable (macOS Monterey+ requis).",
        )
    import subprocess

    try:
        completed = subprocess.run(
            [bin_path, *args],
            input=input_bytes,
            capture_output=True,
            timeout=timeout,
            check=False,
            env={
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "HOME": str(Path.home()),
                "TMPDIR": "/tmp",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
        )
    except subprocess.TimeoutExpired as exc:
        raise AppleShortcutsError(
            "timeout",
            f"Le CLI shortcuts a dépassé {timeout:.0f}s.",
        ) from exc
    except OSError as exc:
        raise AppleShortcutsError(
            "cli_error",
            f"Impossible d'exécuter shortcuts : {exc}",
        ) from exc

    stdout = (completed.stdout or b"").decode("utf-8", errors="replace")
    stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
    return completed.returncode, stdout, stderr


def list_installed(*, folder: str | None = None) -> list[dict[str, str]]:
    """Liste les raccourcis installés sur la machine (lecture seule)."""
    if not is_macos():
        raise AppleShortcutsError("not_macos", "Raccourcis Apple réservés à macOS.")
    args = ["list"]
    if folder:
        args.extend(["--folder-name", folder.strip()])
    code, stdout, stderr = _run_cli(args, timeout=20.0)
    if code != 0:
        raise AppleShortcutsError(
            "list_failed",
            (stderr or stdout or "Échec de shortcuts list").strip()[:500],
        )
    names: list[dict[str, str]] = []
    for line in stdout.splitlines():
        name = line.strip()
        if name:
            names.append({"name": name})
    return names


def list_folders() -> list[str]:
    if not is_macos():
        raise AppleShortcutsError("not_macos", "Raccourcis Apple réservés à macOS.")
    code, stdout, stderr = _run_cli(["list", "--folders"], timeout=20.0)
    if code != 0:
        raise AppleShortcutsError(
            "list_failed",
            (stderr or stdout or "Échec de shortcuts list --folders").strip()[:500],
        )
    return [line.strip() for line in stdout.splitlines() if line.strip()]


def _purge_expired_plans_locked(now: float) -> None:
    expired = [
        plan_id
        for plan_id, plan in _pending_plans.items()
        if plan.expires_at <= now
    ]
    for plan_id in expired:
        _pending_plans.pop(plan_id, None)


def create_plan(
    *,
    shortcut_name: str,
    registry_id: int,
    input_text: str | None,
    allow_input: bool,
    risk: str,
) -> ShortcutPlan:
    """Fige un plan opaque. Aucune exécution."""
    if not is_enabled():
        raise AppleShortcutsError(
            "disabled",
            "Les raccourcis Apple sont désactivés (APPLE_SHORTCUTS_ENABLED).",
        )
    if not is_macos() or resolve_shortcuts_bin() is None:
        raise AppleShortcutsError(
            "unavailable",
            "Le CLI shortcuts n'est pas disponible sur cette machine.",
        )
    name = _validate_shortcut_name(shortcut_name)
    payload = _validate_input(input_text, allow_input=allow_input)
    ttl = max(30, int(getattr(config, "APPLE_SHORTCUTS_PLAN_TTL_SECONDS", 600)))
    now = time.time()
    plan = ShortcutPlan(
        plan_id=secrets.token_urlsafe(24),
        shortcut_name=name,
        registry_id=int(registry_id),
        input_text=payload,
        allow_input=bool(allow_input),
        risk=str(risk or "medium"),
        created_at=now,
        expires_at=now + ttl,
    )
    with _plans_lock:
        _purge_expired_plans_locked(now)
        if len(_pending_plans) >= MAX_PENDING_PLANS:
            oldest_id = min(
                _pending_plans,
                key=lambda key: _pending_plans[key].created_at,
            )
            _pending_plans.pop(oldest_id, None)
            logger.warning(
                "[apple_shortcuts] plan évincé (plafond) plan_id=%s",
                oldest_id[:12],
            )
        _pending_plans[plan.plan_id] = plan
    logger.info(
        "[apple_shortcuts] plan créé name=%r risk=%s plan_id=%s",
        name,
        plan.risk,
        plan.plan_id[:12],
    )
    return plan


def peek_plan(plan_id: str) -> ShortcutPlan | None:
    with _plans_lock:
        _purge_expired_plans_locked(time.time())
        return _pending_plans.get(str(plan_id or ""))


def revoke_plan(plan_id: str) -> bool:
    with _plans_lock:
        return _pending_plans.pop(str(plan_id or ""), None) is not None


def consume_plan(plan_id: str) -> ShortcutPlan:
    with _plans_lock:
        _purge_expired_plans_locked(time.time())
        plan = _pending_plans.pop(str(plan_id or ""), None)
    if plan is None:
        raise AppleShortcutsError(
            "plan_not_found",
            "Plan de raccourci introuvable, expiré ou déjà consommé.",
        )
    return plan


def _write_input_file(text: str) -> Path:
    directory = workspace_dir()
    fd, raw_path = tempfile.mkstemp(
        prefix="shortcut_input_",
        suffix=".txt",
        dir=str(directory),
    )
    path = Path(raw_path)
    try:
        os.chmod(path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise
    return path


def run_shortcut(
    name: str,
    *,
    input_text: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Exécute un raccourci déjà autorisé (après confirmation)."""
    if not is_enabled():
        raise AppleShortcutsError(
            "disabled",
            "Les raccourcis Apple sont désactivés (APPLE_SHORTCUTS_ENABLED).",
        )
    if not is_macos():
        raise AppleShortcutsError("not_macos", "Raccourcis Apple réservés à macOS.")

    shortcut = _validate_shortcut_name(name)
    payload = _validate_input(input_text, allow_input=input_text is not None)
    run_timeout = float(
        timeout
        if timeout is not None
        else getattr(config, "APPLE_SHORTCUTS_RUN_TIMEOUT", DEFAULT_RUN_TIMEOUT)
    )
    run_timeout = max(5.0, min(run_timeout, 300.0))

    args = ["run", shortcut]
    input_path: Path | None = None
    output_path = workspace_dir() / f"shortcut_output_{secrets.token_hex(8)}.txt"
    try:
        if payload is not None:
            input_path = _write_input_file(payload)
            args.extend(["--input-path", str(input_path)])
        args.extend(["--output-path", str(output_path)])
        code, stdout, stderr = _run_cli(args, timeout=run_timeout)
        output_text = ""
        if output_path.is_file():
            output_text = output_path.read_text(encoding="utf-8", errors="replace")
        elif stdout.strip():
            output_text = stdout
        if len(output_text) > MAX_OUTPUT_CHARS:
            output_text = output_text[:MAX_OUTPUT_CHARS] + "…"
        if code != 0:
            detail = (stderr or stdout or "échec shortcuts run").strip()[:500]
            raise AppleShortcutsError("run_failed", detail)
        return {
            "ok": True,
            "shortcut_name": shortcut,
            "output": output_text,
            "message": f"Raccourci « {shortcut} » exécuté.",
        }
    finally:
        if input_path is not None:
            input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


async def list_installed_async(*, folder: str | None = None) -> list[dict[str, str]]:
    return await asyncio.to_thread(list_installed, folder=folder)


async def list_folders_async() -> list[str]:
    return await asyncio.to_thread(list_folders)


async def run_shortcut_async(
    name: str,
    *,
    input_text: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        run_shortcut,
        name,
        input_text=input_text,
        timeout=timeout,
    )


def reset_plans_for_tests() -> None:
    """Vide le registre de plans (tests uniquement)."""
    with _plans_lock:
        _pending_plans.clear()

"""Planification et exécution confinée des commandes shell issues d'un LLM.

Ce module n'exécute jamais une chaîne avec un shell. Une proposition est
analysée, placée dans un registre opaque à durée courte, puis doit être
confirmée avant d'être consommée une seule fois.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import secrets
import shlex
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

_SHELL_META_RE = re.compile(r"""[;&|`$<>\n\r\x00]""")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(api[_-]?key|token|password|passwd|secret|private[_-]?key)\s*="
)
_SENSITIVE_PATH_PARTS = frozenset(
    {
        ".aws",
        ".env",
        ".git",
        ".gnupg",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".ssh",
        "credentials",
        "google-services.json",
        "keychain",
        "secrets",
        "signing.properties",
    }
)

# Capacités volontairement étroites. Les interpréteurs, gestionnaires de
# paquets, outils réseau et commandes de processus sont absents par défaut.
_COMMAND_CAPABILITIES: dict[str, tuple[str, str, bool]] = {
    "pwd": ("inspect_workspace", "low", False),
    "ls": ("inspect_workspace", "low", False),
    "rg": ("search_workspace", "low", False),
    "grep": ("search_workspace", "low", False),
    "find": ("search_workspace", "low", False),
    "cat": ("read_workspace", "low", False),
    "head": ("read_workspace", "low", False),
    "tail": ("read_workspace", "low", False),
    "wc": ("read_workspace", "low", False),
    "file": ("read_workspace", "low", False),
    "stat": ("read_workspace", "low", False),
    "du": ("read_workspace", "low", False),
    "sort": ("transform_output", "low", False),
    "uniq": ("transform_output", "low", False),
    "cut": ("transform_output", "low", False),
    "tr": ("transform_output", "low", False),
    "diff": ("compare_workspace", "low", False),
    "git": ("inspect_git", "low", False),
    "mkdir": ("write_workspace", "medium", True),
    "touch": ("write_workspace", "medium", True),
    "cp": ("write_workspace", "medium", True),
    "mv": ("write_workspace", "medium", True),
}

_EXECUTABLE_CANDIDATES: dict[str, tuple[str, ...]] = {
    name: (
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
        f"/usr/bin/{name}",
        f"/bin/{name}",
    )
    for name in _COMMAND_CAPABILITIES
}

_ALLOWED_GIT_SUBCOMMANDS = frozenset(
    {"diff", "log", "rev-parse", "show", "status"}
)
_FORBIDDEN_GIT_ARGS = frozenset(
    {
        "-C",
        "-c",
        "--config-env",
        "--exec-path",
        "--ext-diff",
        "--git-dir",
        "--output",
        "--paginate",
        "--super-prefix",
        "--textconv",
        "--work-tree",
    }
)
_FORBIDDEN_FIND_ARGS = frozenset(
    {
        "-delete",
        "-exec",
        "-execdir",
        "-fls",
        "-fprint",
        "-fprintf",
        "-ok",
        "-okdir",
    }
)
_FORBIDDEN_RG_ARGS = frozenset({"--pre", "--hostname-bin"})


class ShellPlanError(ValueError):
    """Le plan proposé ne respecte pas la politique d'exécution."""


@dataclass(frozen=True)
class PlannedCommand:
    raw: str
    argv: tuple[str, ...]
    executable: str
    capability: str
    risk: str
    writes: bool

    def public_view(self) -> dict[str, Any]:
        return {
            "command": self.raw,
            "executable": self.executable,
            "capability": self.capability,
            "risk": self.risk,
            "writes": self.writes,
        }


@dataclass(frozen=True)
class ShellPlan:
    plan_id: str
    commands: tuple[PlannedCommand, ...]
    workspace: Path
    timeout: int
    created_at: float
    expires_at: float

    def public_view(self) -> dict[str, Any]:
        writes = sum(1 for command in self.commands if command.writes)
        max_risk = "medium" if writes else "low"
        return {
            "plan_id": self.plan_id,
            "commands": [command.public_view() for command in self.commands],
            "workspace": str(self.workspace),
            "expires_in_seconds": max(0, math.ceil(self.expires_at - time.monotonic())),
            "impact_analysis": {
                "max_risk": max_risk,
                "command_count": len(self.commands),
                "read_only_commands": len(self.commands) - writes,
                "workspace_write_commands": writes,
                "network_access": False,
                "home_access": False,
                "secret_access": False,
                "system_process_access": False,
                "shell_expansion": False,
                "isolation": "dedicated_workspace",
            },
        }


_pending_plans: dict[str, ShellPlan] = {}
_pending_plans_lock = threading.Lock()


def _workspace_root() -> Path:
    configured = getattr(
        config,
        "LLM_SHELL_WORKSPACE",
        str(config.BASE_DIR / "data" / "llm_shell_workspace"),
    )
    root = Path(str(configured)).expanduser().resolve()
    home = Path.home().resolve()
    if root == home:
        raise ShellPlanError("LLM_SHELL_WORKSPACE ne peut pas être le dossier personnel")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def _contains_sensitive_path(token: str) -> bool:
    normalized = token.lower().replace("\\", "/")
    parts = {
        part
        for part in re.split(r"[/=:]", normalized)
        if part
    }
    if parts & _SENSITIVE_PATH_PARTS:
        return True
    if any(
        part.startswith((".env.", ".env-", "credentials.", "secrets."))
        for part in parts
    ):
        return True
    return any(
        normalized.endswith(suffix)
        for suffix in (".key", ".pem", ".p12", ".pfx", ".mobileprovision")
    )


def _validate_path_token(token: str, workspace: Path) -> None:
    value = token.split("=", 1)[1] if "=" in token else token
    if not value or value == "-":
        return
    if value.startswith("~"):
        raise ShellPlanError("accès au dossier personnel interdit")
    if _contains_sensitive_path(value):
        raise ShellPlanError("accès à un chemin sensible interdit")

    looks_like_path = (
        value.startswith(("/", "."))
        or "/" in value
        or "\\" in value
        or any(char in value for char in ("*", "?"))
    )
    if not looks_like_path:
        return
    candidate = Path(value)
    if candidate.is_absolute():
        raise ShellPlanError("chemins absolus interdits")
    if ".." in candidate.parts:
        raise ShellPlanError("sortie du workspace interdite")
    resolved = (workspace / candidate).resolve(strict=False)
    if not resolved.is_relative_to(workspace):
        raise ShellPlanError("sortie du workspace interdite")


def _validate_command_specific(executable: str, args: tuple[str, ...]) -> None:
    if executable == "git":
        if not args or args[0] not in _ALLOWED_GIT_SUBCOMMANDS:
            allowed = ", ".join(sorted(_ALLOWED_GIT_SUBCOMMANDS))
            raise ShellPlanError(f"sous-commande git interdite (autorisées: {allowed})")
        for arg in args[1:]:
            flag = arg.split("=", 1)[0]
            if flag in _FORBIDDEN_GIT_ARGS:
                raise ShellPlanError(f"option git interdite: {flag}")

    if executable == "find":
        for arg in args:
            flag = arg.split("=", 1)[0].lower()
            if any(flag.startswith(forbidden) for forbidden in _FORBIDDEN_FIND_ARGS):
                raise ShellPlanError(f"action find interdite: {flag}")

    if executable == "rg":
        for arg in args:
            flag = arg.split("=", 1)[0]
            if flag in _FORBIDDEN_RG_ARGS:
                raise ShellPlanError(f"option rg interdite: {flag}")

    if executable in {"sort", "diff"}:
        for arg in args:
            flag = arg.split("=", 1)[0]
            if flag in {"-o", "--output"}:
                raise ShellPlanError(f"écriture via {executable} interdite")
            if executable == "sort" and flag == "--compress-program":
                raise ShellPlanError("programme externe via sort interdit")

    if executable == "tail" and any(
        arg.split("=", 1)[0] == "--pid" for arg in args
    ):
        raise ShellPlanError("observation de processus via tail interdite")

    if executable in {"cp", "mv"}:
        allowed_flags = {"-n", "-v", "--no-clobber", "--verbose"}
        unknown = [arg for arg in args if arg.startswith("-") and arg not in allowed_flags]
        if unknown:
            raise ShellPlanError(f"option {executable} interdite: {unknown[0]}")

    if executable == "mkdir":
        allowed_flags = {"-p", "-v", "--parents", "--verbose"}
        unknown = [arg for arg in args if arg.startswith("-") and arg not in allowed_flags]
        if unknown:
            raise ShellPlanError(f"option mkdir interdite: {unknown[0]}")

    if executable == "touch" and any(arg.startswith("-") for arg in args):
        raise ShellPlanError("options touch interdites")


def analyze_command(raw_command: str, *, workspace: Path) -> PlannedCommand:
    """Analyse une commande unique sans jamais l'exécuter."""
    raw = str(raw_command or "").strip()
    if not raw:
        raise ShellPlanError("commande vide")
    if len(raw) > 2000:
        raise ShellPlanError("commande trop longue")
    if _SHELL_META_RE.search(raw):
        raise ShellPlanError("métacaractères ou expansion shell interdits")
    if _SECRET_ASSIGNMENT_RE.search(raw):
        raise ShellPlanError("secret en ligne de commande interdit")

    try:
        argv = tuple(shlex.split(raw, posix=True))
    except ValueError as exc:
        raise ShellPlanError(f"syntaxe invalide: {exc}") from exc
    if not argv:
        raise ShellPlanError("commande vide")

    executable = argv[0]
    if "/" in executable or executable not in _COMMAND_CAPABILITIES:
        allowed = ", ".join(sorted(_COMMAND_CAPABILITIES))
        raise ShellPlanError(f"exécutable interdit: {executable!r} (allowlist: {allowed})")

    args = argv[1:]
    _validate_command_specific(executable, args)
    for arg in args:
        _validate_path_token(arg, workspace)

    capability, risk, writes = _COMMAND_CAPABILITIES[executable]
    return PlannedCommand(
        raw=raw,
        argv=argv,
        executable=executable,
        capability=capability,
        risk=risk,
        writes=writes,
    )


def prepare_shell_plan(commands: list[str], *, timeout: int = 120) -> dict[str, Any]:
    """Valide et enregistre un plan opaque, sans exécuter de commande."""
    max_commands = max(1, int(getattr(config, "LLM_SHELL_MAX_COMMANDS", 8)))
    if not commands:
        raise ShellPlanError("aucune commande proposée")
    if len(commands) > max_commands:
        raise ShellPlanError(f"trop de commandes (maximum {max_commands})")

    plan_id = secrets.token_urlsafe(24)
    workspace = (_workspace_root() / plan_id).resolve()
    analyzed = tuple(analyze_command(command, workspace=workspace) for command in commands)
    workspace.mkdir(parents=True, exist_ok=False, mode=0o700)

    max_timeout = max(1, int(getattr(config, "LLM_SHELL_MAX_TIMEOUT", 120)))
    ttl = max(30, int(getattr(config, "LLM_SHELL_PLAN_TTL_SECONDS", 600)))
    now = time.monotonic()
    plan = ShellPlan(
        plan_id=plan_id,
        commands=analyzed,
        workspace=workspace,
        timeout=max(1, min(int(timeout), max_timeout)),
        created_at=now,
        expires_at=now + ttl,
    )
    with _pending_plans_lock:
        expired = [
            key for key, value in _pending_plans.items() if value.expires_at <= now
        ]
        for key in expired:
            _pending_plans.pop(key, None)
        _pending_plans[plan_id] = plan
    logger.info(
        "Plan shell créé id=%s commandes=%d risque=%s",
        plan_id[:8],
        len(analyzed),
        plan.public_view()["impact_analysis"]["max_risk"],
    )
    return plan.public_view()


def get_shell_plan(plan_id: str) -> dict[str, Any]:
    """Retourne la vue publique d'un plan encore valide."""
    now = time.monotonic()
    with _pending_plans_lock:
        plan = _pending_plans.get(plan_id)
        if not plan or plan.expires_at <= now:
            _pending_plans.pop(plan_id, None)
            raise ShellPlanError("plan shell inconnu, expiré ou déjà utilisé")
        return plan.public_view()


def revoke_shell_plan(plan_id: str) -> bool:
    """Révoque un plan encore en attente, notamment après annulation UI."""
    with _pending_plans_lock:
        return _pending_plans.pop(str(plan_id or ""), None) is not None


def _consume_shell_plan(plan_id: str) -> ShellPlan:
    now = time.monotonic()
    with _pending_plans_lock:
        plan = _pending_plans.pop(plan_id, None)
    if not plan or plan.expires_at <= now:
        raise ShellPlanError("plan shell inconnu, expiré ou déjà utilisé")
    return plan


def _resolve_executable(name: str) -> str:
    for candidate in _EXECUTABLE_CANDIDATES.get(name, ()):
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    resolved = shutil.which(
        name,
        path="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
    )
    if not resolved:
        raise ShellPlanError(f"exécutable allowlisté introuvable: {name}")
    return resolved


def _safe_environment(workspace: Path) -> dict[str, str]:
    temp_dir = workspace / "tmp"
    temp_dir.mkdir(exist_ok=True, mode=0o700)
    return {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(workspace),
        "TMPDIR": str(temp_dir),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "NO_COLOR": "1",
    }


async def execute_shell_plan(plan_id: str) -> dict[str, Any]:
    """Consomme puis exécute exactement le plan confirmé, sans shell."""
    plan = _consume_shell_plan(plan_id)
    outputs: list[str] = []
    code_blocks: list[dict[str, str]] = []
    errors: list[str] = []

    for command in plan.commands:
        outputs.append(f"$ {command.raw}")
        executable = _resolve_executable(command.executable)
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                *command.argv[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(plan.workspace),
                env=_safe_environment(plan.workspace),
                start_new_session=True,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=plan.timeout,
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass
            errors.append(f"{command.executable}: timeout après {plan.timeout}s")
            break
        except (OSError, ShellPlanError) as exc:
            errors.append(f"{command.executable}: {exc}")
            break

        out = stdout.decode("utf-8", errors="replace")[:3000]
        err = stderr.decode("utf-8", errors="replace")[:1000]
        if out:
            outputs.append(out)
        if err:
            errors.append(err)
        code_blocks.append({"language": "shell", "code": command.raw})
        if process.returncode != 0:
            errors.append(
                f"{command.executable}: code de sortie {process.returncode}"
            )
            break

    impact = plan.public_view()["impact_analysis"]
    logger.info(
        "Plan shell consommé id=%s ok=%s commandes_exécutées=%d",
        plan.plan_id[:8],
        not errors,
        len(code_blocks),
    )
    return {
        "ok": not errors,
        "output": "\n".join(outputs)[:5000],
        "code": code_blocks,
        "errors": errors[:3],
        "summary": "Plan exécuté dans le workspace isolé."
        if not errors
        else "Plan interrompu à la première erreur.",
        "workspace": str(plan.workspace),
        "impact_analysis": impact,
    }


def reset_shell_plans_for_tests() -> None:
    """Vide le registre en mémoire. Réservé aux tests."""
    with _pending_plans_lock:
        _pending_plans.clear()

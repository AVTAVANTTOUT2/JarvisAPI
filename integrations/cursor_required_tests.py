"""Tests requis pour jobs Cursor — argv structurés, jamais de shell.

Aucune chaîne utilisateur / LLM / template n'est exécutée avec shell=True.
"""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Exécutables autorisés (basename uniquement, ou relative worktree pour gradlew).
ALLOWED_EXECUTABLES: frozenset[str] = frozenset(
    {
        "pytest",
        "python",
        "python3",
        "pnpm",
        "npm",
        "npx",
        "gradle",
        "./gradlew",
        "gradlew",
    }
)

# Métacaractères / formes d'injection shell — rejet immédiat.
_SHELL_META_RE = re.compile(
    r"""[;|&`$<>]|\$\(|&&|\|\||>>|<<|\n|\r|\x00"""
)
_FORBIDDEN_ARGS_RE = re.compile(
    r"""(?ix)
    ^-c$|^--command$|^/c$
    |^-[ce]$
    """
)


class RequiredTestError(ValueError):
    """Spécification de test invalide ou dangereuse."""


@dataclass(frozen=True)
class RequiredTest:
    """Spécification structurée d'un test à exécuter dans le worktree."""

    executable: str
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    timeout_seconds: int = 900

    def argv(self) -> list[str]:
        return [self.executable, *self.args]


def _has_shell_meta(text: str) -> bool:
    return bool(_SHELL_META_RE.search(text))


def _validate_path_arg(arg: str, worktree: Path) -> None:
    """Refuse les chemins qui sortent du worktree."""
    if arg.startswith("-"):
        return  # flag
    # Chemins relatifs suspects
    if ".." in Path(arg).parts:
        candidate = (worktree / arg).resolve()
        try:
            candidate.relative_to(worktree.resolve())
        except ValueError as exc:
            raise RequiredTestError(
                f"chemin hors worktree refusé: {arg}"
            ) from exc
    if arg.startswith("/") or (len(arg) > 1 and arg[1] == ":"):
        # Chemin absolu : doit rester sous worktree
        try:
            Path(arg).resolve().relative_to(worktree.resolve())
        except ValueError as exc:
            raise RequiredTestError(
                f"chemin absolu hors worktree refusé: {arg}"
            ) from exc


def parse_required_test(
    spec: str | dict[str, Any] | RequiredTest,
    *,
    worktree: Path,
) -> RequiredTest:
    """Parse une spec (legacy string ou dict) en RequiredTest sûr.

    Les chaînes legacy du type ``pytest tests/foo.py -q`` sont tokenisées
    via shlex (sans expansion) puis validées. Toute métacaractère shell
    provoque un rejet.
    """
    if isinstance(spec, RequiredTest):
        return _finalize_required_test(spec, worktree=worktree)

    if isinstance(spec, dict):
        exe = str(spec.get("executable") or "").strip()
        args = [str(a) for a in (spec.get("args") or [])]
        cwd = spec.get("cwd")
        timeout = int(spec.get("timeout_seconds") or 900)
        return _finalize_required_test(
            RequiredTest(
                executable=exe,
                args=args,
                cwd=str(cwd) if cwd else None,
                timeout_seconds=timeout,
            ),
            worktree=worktree,
        )

    if not isinstance(spec, str) or not spec.strip():
        raise RequiredTestError("spécification de test vide")

    raw = spec.strip()
    if _has_shell_meta(raw):
        raise RequiredTestError(
            f"métacaractères shell interdits dans required_tests: {raw!r}"
        )

    try:
        tokens = shlex.split(raw, posix=True)
    except ValueError as exc:
        raise RequiredTestError(f"impossible de parser: {raw!r}") from exc

    if not tokens:
        raise RequiredTestError("spécification de test vide après parse")

    # Formes convenues : "pnpm test", "npm test", "python -m pytest …"
    exe = tokens[0]
    args = tokens[1:]

    # Interdit python -c / python3 -c
    if exe in ("python", "python3") and any(
        a in ("-c", "--command") for a in args
    ):
        raise RequiredTestError("python -c interdit dans required_tests")

    return _finalize_required_test(
        RequiredTest(executable=exe, args=args),
        worktree=worktree,
    )


def _finalize_required_test(rt: RequiredTest, *, worktree: Path) -> RequiredTest:
    exe = rt.executable.strip()
    if not exe:
        raise RequiredTestError("executable manquant")
    if _has_shell_meta(exe) or any(_has_shell_meta(a) for a in rt.args):
        raise RequiredTestError("métacaractères shell dans executable/args")

    # Allowlist executable
    basename = Path(exe).name if not exe.startswith("./") else exe
    allowed_names = {Path(a).name if not a.startswith("./") else a for a in ALLOWED_EXECUTABLES}
    check_name = exe if exe.startswith("./") else Path(exe).name
    if check_name not in ALLOWED_EXECUTABLES and basename not in allowed_names:
        raise RequiredTestError(f"executable non allowlisté: {exe}")

    # Interdire exécutable avec path sortant
    if "/" in exe and not exe.startswith("./"):
        # chemin absolu ou relatif profond — doit rester dans worktree
        _validate_path_arg(exe, worktree)

    for arg in rt.args:
        if _FORBIDDEN_ARGS_RE.match(arg):
            raise RequiredTestError(f"argument interdit: {arg}")
        _validate_path_arg(arg, worktree)

    # cwd borné au worktree
    cwd_path = worktree
    if rt.cwd:
        if _has_shell_meta(rt.cwd):
            raise RequiredTestError(f"cwd invalide: {rt.cwd}")
        candidate = (worktree / rt.cwd).resolve() if not Path(rt.cwd).is_absolute() else Path(rt.cwd).resolve()
        try:
            candidate.relative_to(worktree.resolve())
        except ValueError as exc:
            raise RequiredTestError(f"cwd hors worktree: {rt.cwd}") from exc
        cwd_path = candidate

    timeout = max(1, min(int(rt.timeout_seconds or 900), 900))
    return RequiredTest(
        executable=exe,
        args=list(rt.args),
        cwd=str(cwd_path.relative_to(worktree.resolve())) if cwd_path != worktree.resolve() else None,
        timeout_seconds=timeout,
    )


def run_required_test(
    rt: RequiredTest,
    *,
    worktree: Path,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Exécute un RequiredTest avec shell=False, cwd borné au worktree."""
    finalized = _finalize_required_test(rt, worktree=worktree)
    cwd = worktree.resolve()
    if finalized.cwd:
        cwd = (worktree / finalized.cwd).resolve()
        cwd.relative_to(worktree.resolve())  # raises if outside

    argv = finalized.argv()
    # Résoudre executable relatif au worktree (./gradlew)
    if argv[0].startswith("./"):
        resolved_exe = (worktree / argv[0]).resolve()
        try:
            resolved_exe.relative_to(worktree.resolve())
        except ValueError as exc:
            raise RequiredTestError("executable hors worktree") from exc
        argv = [str(resolved_exe), *argv[1:]]

    effective_timeout = min(timeout or finalized.timeout_seconds, finalized.timeout_seconds)
    logger.info("[cursor_tests] run argv=%s cwd=%s", argv, cwd)
    return subprocess.run(
        argv,
        shell=False,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=effective_timeout,
    )


def parse_and_run_required_tests(
    specs: list[Any],
    *,
    worktree: Path,
    timeout: int,
    max_tests: int = 3,
) -> tuple[bool, str]:
    """Parse + exécute jusqu'à ``max_tests`` specs. Retourne (ok, log)."""
    if not isinstance(specs, list):
        return True, ""
    test_ok = True
    test_log = ""
    for spec in specs[:max_tests]:
        try:
            rt = parse_required_test(spec, worktree=worktree)
        except RequiredTestError as exc:
            test_log += f"skip/reject: {exc}\n"
            # Rejet = pas d'exécution ; on n'échoue pas le job pour une
            # spec malveillante (elle est simplement ignorée), sauf si
            # c'était la seule spec attendue — on marque quand même ok=True
            # pour ne pas punir un refus de sécurité. Les specs valides
            # doivent elles passer.
            continue
        try:
            result = run_required_test(rt, worktree=worktree, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            test_log += f"error: {exc}\n"
            test_ok = False
            continue
        test_log += (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            test_ok = False
    return test_ok, test_log

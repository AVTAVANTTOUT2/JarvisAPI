"""Sandbox subprocess isole pour projets DevAgent."""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote

logger = logging.getLogger(__name__)


class ExecutionTimeout(Exception):
    """Levee quand une commande depasse le timeout autorise."""


class GeneratedPathError(ValueError):
    """Levée lorsqu'un chemin de fichier généré sort du sandbox ``src/``."""


_DEVAGENT_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "TMPDIR",
        "TMP",
        "TEMP",
        "USER",
        "LOGNAME",
        "SHELL",
        "TZ",
        "CI",
        "GIT_EDITOR",
        "EDITOR",
        "GIT_TERMINAL_PROMPT",
        "GIT_CONFIG_NOSYSTEM",
        "NO_OPEN_BROWSER",
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    }
)


def _fully_url_decode(path: str) -> str:
    """Décode les variantes URL imbriquées pour détecter les traversées cachées."""
    decoded = path
    for _ in range(8):
        candidate = unquote(decoded)
        if candidate == decoded:
            return decoded
        decoded = candidate
    if unquote(decoded) != decoded:
        raise GeneratedPathError("Chemin généré excessivement encodé")
    return decoded


def resolve_generated_path(project_path: Path, relative_path: str) -> Path:
    """Résout un chemin LLM et garantit son confinement dans ``project/src``.

    Les chemins absolus, les composants ``..`` (même encodés) et les liens
    symboliques sortants sont refusés avant toute création de répertoire.
    """
    if not isinstance(relative_path, str):
        raise GeneratedPathError("Le chemin généré doit être une chaîne")
    raw_path = relative_path.strip()
    if not raw_path or "\x00" in raw_path:
        raise GeneratedPathError("Chemin généré vide ou invalide")

    decoded_path = _fully_url_decode(raw_path)
    normalized_path = decoded_path.replace("\\", "/")
    if "\x00" in normalized_path:
        raise GeneratedPathError("Chemin généré contenant un octet nul")

    posix_path = PurePosixPath(normalized_path)
    windows_path = PureWindowsPath(normalized_path)
    if posix_path.is_absolute() or windows_path.is_absolute():
        raise GeneratedPathError(f"Chemin généré absolu refusé : {relative_path!r}")
    if ".." in posix_path.parts:
        raise GeneratedPathError(f"Composante '..' refusée : {relative_path!r}")

    project_root = project_path.resolve()
    src_root = (project_root / "src").resolve()
    if not src_root.is_relative_to(project_root):
        raise GeneratedPathError("Le dossier src du projet sort du sandbox")

    target = (src_root / Path(normalized_path)).resolve()
    if not target.is_relative_to(src_root):
        raise GeneratedPathError(f"Chemin généré hors de src : {relative_path!r}")
    return target


def build_devagent_safe_env(
    *,
    isolated_home: Path,
    extra: Mapping[str, str] | None = None,
    parent_environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Construit l'environnement allowlisté transmis aux subprocess DevAgent."""
    parent = parent_environ if parent_environ is not None else os.environ
    isolated_home.mkdir(parents=True, exist_ok=True)
    temp_dir = isolated_home / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    safe_env = {
        "PATH": parent.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "HOME": str(isolated_home),
        "LANG": parent.get("LANG") or "fr_FR.UTF-8",
        "LC_ALL": parent.get("LC_ALL") or parent.get("LANG") or "fr_FR.UTF-8",
        "TERM": "dumb",
        "TMPDIR": str(temp_dir),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "NO_OPEN_BROWSER": "1",
        "GIT_AUTHOR_NAME": "DevAgent",
        "GIT_AUTHOR_EMAIL": "devagent@localhost",
        "GIT_COMMITTER_NAME": "DevAgent",
        "GIT_COMMITTER_EMAIL": "devagent@localhost",
    }
    for key in ("LC_CTYPE", "USER", "LOGNAME", "SHELL", "TZ", "CI"):
        value = parent.get(key)
        if value:
            safe_env[key] = value

    if extra:
        forbidden = sorted(set(extra) - _DEVAGENT_ENV_ALLOWLIST)
        if forbidden:
            raise ValueError(
                "Variables d'environnement DevAgent non autorisées : "
                + ", ".join(forbidden)
            )
        safe_env.update({str(key): str(value) for key, value in extra.items()})

    return {key: value for key, value in safe_env.items() if key in _DEVAGENT_ENV_ALLOWLIST}


def run_isolated(
    command: str | Sequence[str],
    cwd: Path,
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> dict[str, str | int]:
    """Execute une commande dans le repertoire isole du projet.

    ``env`` (optionnel) ne peut surcharger que l'allowlist explicite — utile
    pour ``GIT_EDITOR=true`` lors d'un ``git rebase --continue``. Les clés API,
    jetons et secrets du processus parent ne sont jamais transmis.
    """
    if isinstance(command, str):
        args = command.split()
    else:
        args = list(command)

    resolved_cwd = cwd.resolve()
    if not resolved_cwd.exists():
        raise FileNotFoundError(f"Repertoire projet introuvable : {resolved_cwd}")

    with tempfile.TemporaryDirectory(prefix="jarvis-devagent-home-") as temp_home:
        safe_env = build_devagent_safe_env(
            isolated_home=Path(temp_home),
            extra=env,
        )
        try:
            result = subprocess.run(
                args,
                cwd=str(resolved_cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=safe_env,
                check=False,
            )
            return {
                "returncode": result.returncode,
                "stdout": result.stdout or "",
                "stderr": result.stderr or "",
            }
        except subprocess.TimeoutExpired as exc:
            raise ExecutionTimeout(
                f"Commande depassee {timeout}s : {' '.join(args)}"
            ) from exc


def git_current_sha(project_path: Path) -> str | None:
    """SHA du commit HEAD, ou None si le dépôt n'a pas encore de commit."""
    result = run_isolated(["git", "rev-parse", "HEAD"], cwd=project_path, timeout=10)
    if result["returncode"] != 0:
        return None
    return result["stdout"].strip() or None


def git_log_range(project_path: Path, base: str = "", head: str = "HEAD") -> str:
    """Log oneline entre ``base`` (exclu) et ``head``. ``base`` vide = tout l'historique."""
    rev_range = f"{base}..{head}" if base else head
    result = run_isolated(
        ["git", "log", "--oneline", "--no-decorate", rev_range], cwd=project_path, timeout=15,
    )
    return result["stdout"] if result["returncode"] == 0 else ""


def git_diff_stat(project_path: Path, base: str = "", head: str = "HEAD") -> str:
    """Statistiques de diff (fichiers touchés, +/-) entre ``base`` et ``head``."""
    rev_range = f"{base}..{head}" if base else head
    result = run_isolated(["git", "diff", "--stat", rev_range], cwd=project_path, timeout=15)
    return result["stdout"] if result["returncode"] == 0 else ""


def setup_venv(project_path: Path, timeout: int = 120) -> dict[str, str | int]:
    """Cree un venv dedie dans le projet."""
    venv_path = project_path / "venv"
    if venv_path.exists():
        return {"returncode": 0, "stdout": "venv deja present", "stderr": ""}
    return run_isolated(
        ["python3", "-m", "venv", str(venv_path)],
        cwd=project_path,
        timeout=timeout,
    )


def git_init(project_path: Path) -> None:
    """Initialise un depot git isole."""
    git_dir = project_path / ".git"
    if git_dir.exists():
        return
    run_isolated(["git", "init"], cwd=project_path)
    run_isolated(["git", "add", "-A"], cwd=project_path)
    run_isolated(["git", "commit", "-m", "init"], cwd=project_path)


def git_commit(project_path: Path, message: str) -> dict[str, str | int]:
    """Commit automatique apres iteration reussie."""
    add_result = run_isolated(["git", "add", "-A"], cwd=project_path)
    if add_result["returncode"] != 0:
        return add_result
    return run_isolated(["git", "commit", "-m", message], cwd=project_path)

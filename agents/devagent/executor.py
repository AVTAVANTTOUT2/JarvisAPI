"""Sandbox subprocess isole pour projets DevAgent."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)


class ExecutionTimeout(Exception):
    """Levee quand une commande depasse le timeout autorise."""


def run_isolated(
    command: str | Sequence[str],
    cwd: Path,
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> dict[str, str | int]:
    """Execute une commande dans le repertoire isole du projet.

    ``env`` (optionnel) est fusionné par-dessus l'environnement courant —
    utile pour ``GIT_EDITOR=true`` lors d'un ``git rebase --continue`` sans
    éditeur interactif disponible.
    """
    if isinstance(command, str):
        args = command.split()
    else:
        args = list(command)

    resolved_cwd = cwd.resolve()
    if not resolved_cwd.exists():
        raise FileNotFoundError(f"Repertoire projet introuvable : {resolved_cwd}")

    full_env = {**os.environ, **env} if env else None

    try:
        result = subprocess.run(
            args,
            cwd=str(resolved_cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=full_env,
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

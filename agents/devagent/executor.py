"""Sandbox subprocess isole pour projets DevAgent."""

from __future__ import annotations

import logging
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
) -> dict[str, str | int]:
    """Execute une commande dans le repertoire isole du projet."""
    if isinstance(command, str):
        args = command.split()
    else:
        args = list(command)

    resolved_cwd = cwd.resolve()
    if not resolved_cwd.exists():
        raise FileNotFoundError(f"Repertoire projet introuvable : {resolved_cwd}")

    try:
        result = subprocess.run(
            args,
            cwd=str(resolved_cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
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

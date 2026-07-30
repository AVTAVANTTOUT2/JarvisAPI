"""Garde-fous qualité progressifs pour les fichiers modifiés.

Le dépôt historique n'est pas encore intégralement normalisé. La CI applique
donc Ruff, Black, mypy, Bandit et Semgrep à chaque fichier touché, tout en
rejouant mypy sur un noyau annoté permanent. La dette existante ne bloque pas
les corrections, mais aucune nouvelle modification ne peut l'agrandir.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MYPY_BASELINE = (
    "database/time_buckets.py",
    "jarvis/events.py",
    "pipeline.py",
    "security_headers.py",
)
SEMGREP_SUFFIXES = {".js", ".jsx", ".kt", ".py", ".ts", ".tsx"}


def changed_files(base: str, *, root: Path = ROOT) -> list[str]:
    """Retourne les fichiers ajoutés ou modifiés depuis ``base``."""
    result = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            f"{base}...HEAD",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line and (root / line).is_file()]


def _run(command: Sequence[str], *, root: Path = ROOT) -> int:
    print(f"+ {' '.join(command)}", flush=True)
    try:
        return subprocess.run(command, cwd=root, check=False).returncode
    except FileNotFoundError:
        print(f"outil introuvable : {command[0]}", flush=True)
        return 127


def quality_commands(files: Sequence[str]) -> list[list[str]]:
    """Construit les commandes applicables aux fichiers modifiés."""
    python_files = sorted(path for path in files if path.endswith(".py"))
    production_python = [
        path
        for path in python_files
        if not path.startswith(("tests/", "jarvis/tests/", "agents/devagent/tests/"))
    ]
    semgrep_files = sorted(path for path in files if Path(path).suffix.lower() in SEMGREP_SUFFIXES)

    commands: list[list[str]] = []
    if python_files:
        commands.extend(
            [
                ["ruff", "check", "--output-format=github", *python_files],
                ["black", "--check", "--diff", *python_files],
            ]
        )

    mypy_files = sorted(set(MYPY_BASELINE).union(production_python))
    commands.append(["mypy", "--config-file", "pyproject.toml", *mypy_files])

    if production_python:
        commands.append(
            [
                "bandit",
                "-q",
                "-c",
                "pyproject.toml",
                "-ll",
                "-ii",
                *production_python,
            ]
        )

    if semgrep_files:
        commands.append(
            [
                "semgrep",
                "scan",
                "--config",
                ".semgrep.yml",
                "--error",
                "--severity",
                "ERROR",
                "--metrics=off",
                *semgrep_files,
            ]
        )
    return commands


def run_quality_gate(base: str, *, root: Path = ROOT) -> int:
    files = changed_files(base, root=root)
    print(f"{len(files)} fichier(s) modifié(s) depuis {base}", flush=True)
    statuses = [_run(command, root=root) for command in quality_commands(files)]
    return 1 if any(status != 0 for status in statuses) else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base",
        default=os.environ.get("QUALITY_BASE"),
        help="SHA de base Git (ou variable QUALITY_BASE)",
    )
    args = parser.parse_args(argv)
    if not args.base:
        parser.error("--base ou QUALITY_BASE est requis")
    return run_quality_gate(args.base)


if __name__ == "__main__":
    raise SystemExit(main())

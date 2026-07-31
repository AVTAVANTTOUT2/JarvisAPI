#!/usr/bin/env python3
"""Génère les LaunchAgents JARVIS depuis le checkout et le venv réels.

Les fichiers plist launchd ne savent pas développer les variables shell. Ils
doivent donc être générés à l'installation, jamais copiés depuis un fichier
contenant le chemin du poste d'un autre utilisateur.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
LAUNCH_AGENT_FILENAMES = {
    "supervisor": "com.jarvis.supervisor.plist",
    "imessage-daemon": "com.jarvis.imessage-daemon.plist",
}


def _resolved_directory(path: Path | str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(f"Répertoire introuvable : {resolved}")
    return resolved


def _required_file(path: Path, description: str, *, executable: bool = False) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"{description} introuvable : {path}")
    if executable and not os.access(path, os.X_OK):
        raise PermissionError(f"{description} non exécutable : {path}")
    return path


def build_launch_agent_payloads(
    *,
    repo_root: Path | str,
    venv_dir: Path | str,
    supervisor_port: int = 9000,
    imessage_port: int = 8193,
) -> dict[str, dict[str, object]]:
    """Construit les payloads plist et vérifie tous leurs chemins runtime."""
    repo = _resolved_directory(repo_root)
    venv = _resolved_directory(venv_dir)
    python = _required_file(
        venv / "bin" / "python",
        "Interpréteur du venv",
        executable=True,
    )
    supervisor = _required_file(repo / "supervisor.py", "Entrypoint supervisor")
    imessage_daemon = _required_file(
        repo / "scripts" / "imessage_daemon.py",
        "Entrypoint daemon iMessage",
    )
    logs_dir = repo / "data" / "logs"
    environment = {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "PYTHONUNBUFFERED": "1",
    }

    return {
        "supervisor": {
            "Label": "com.jarvis.supervisor",
            "ProgramArguments": [str(python), str(supervisor)],
            "WorkingDirectory": str(repo),
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 5,
            "StandardOutPath": str(logs_dir / "supervisor.log"),
            "StandardErrorPath": str(logs_dir / "supervisor.log"),
            "EnvironmentVariables": {
                **environment,
                "SUPERVISOR_PORT": str(supervisor_port),
            },
        },
        "imessage-daemon": {
            "Label": "com.jarvis.imessage-daemon",
            "ProgramArguments": [
                str(python),
                str(imessage_daemon),
                "--port",
                str(imessage_port),
            ],
            "WorkingDirectory": str(repo),
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 5,
            "StandardOutPath": str(logs_dir / "imessage-daemon.log"),
            "StandardErrorPath": str(logs_dir / "imessage-daemon.log"),
            "EnvironmentVariables": environment,
        },
    }


def validate_launch_agent_payloads(
    payloads: dict[str, dict[str, object]],
    *,
    repo_root: Path | str,
    venv_dir: Path | str,
) -> None:
    """Vérifie ProgramArguments, WorkingDirectory et chemins de logs."""
    repo = _resolved_directory(repo_root)
    venv = _resolved_directory(venv_dir)
    expected_python = venv / "bin" / "python"
    expected_entrypoints = {
        "supervisor": repo / "supervisor.py",
        "imessage-daemon": repo / "scripts" / "imessage_daemon.py",
    }
    expected_logs = {
        "supervisor": repo / "data" / "logs" / "supervisor.log",
        "imessage-daemon": repo / "data" / "logs" / "imessage-daemon.log",
    }

    for service, payload in payloads.items():
        arguments = payload.get("ProgramArguments")
        if not isinstance(arguments, list) or len(arguments) < 2:
            raise ValueError(f"ProgramArguments invalide pour {service}")
        if Path(str(arguments[0])) != expected_python:
            raise ValueError(f"Interpréteur inattendu pour {service}: {arguments[0]}")
        if Path(str(arguments[1])) != expected_entrypoints[service]:
            raise ValueError(f"Entrypoint inattendu pour {service}: {arguments[1]}")
        if Path(str(payload.get("WorkingDirectory", ""))) != repo:
            raise ValueError(f"WorkingDirectory invalide pour {service}")
        for key in ("StandardOutPath", "StandardErrorPath"):
            if Path(str(payload.get(key, ""))) != expected_logs[service]:
                raise ValueError(f"{key} invalide pour {service}")


def validate_plists_with_plutil(paths: Iterable[Path]) -> None:
    """Valide les plists avec l'outil natif lorsque l'hôte est macOS."""
    if sys.platform != "darwin":
        return
    plutil = shutil.which("plutil")
    if plutil is None:
        raise RuntimeError("plutil est obligatoire pour valider les LaunchAgents sur macOS")
    for path in paths:
        result = subprocess.run(
            [plutil, "-lint", str(path)],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"plist invalide ({path}): {detail}")


def write_launch_agents(
    *,
    output_dir: Path | str,
    repo_root: Path | str = PROJECT_DIR,
    venv_dir: Path | str | None = None,
    services: Iterable[str] = ("supervisor", "imessage-daemon"),
    supervisor_port: int = 9000,
    imessage_port: int = 8193,
) -> dict[str, Path]:
    """Écrit les services sélectionnés et retourne leurs chemins."""
    repo = _resolved_directory(repo_root)
    venv = Path(venv_dir).expanduser() if venv_dir is not None else repo / "venv"
    venv = _resolved_directory(venv)
    selected = tuple(dict.fromkeys(services))
    unknown = sorted(set(selected) - set(LAUNCH_AGENT_FILENAMES))
    if unknown:
        raise ValueError(f"Services inconnus : {', '.join(unknown)}")
    if not selected:
        raise ValueError("Sélectionne au moins un service")

    all_payloads = build_launch_agent_payloads(
        repo_root=repo,
        venv_dir=venv,
        supervisor_port=supervisor_port,
        imessage_port=imessage_port,
    )
    payloads = {service: all_payloads[service] for service in selected}
    validate_launch_agent_payloads(payloads, repo_root=repo, venv_dir=venv)

    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    (repo / "data" / "logs").mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for service, payload in payloads.items():
        path = destination / LAUNCH_AGENT_FILENAMES[service]
        with path.open("wb") as handle:
            plistlib.dump(payload, handle, fmt=plistlib.FMT_XML, sort_keys=False)
        path.chmod(0o644)
        written[service] = path

    validate_plists_with_plutil(written.values())
    return written


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "install"))
    parser.add_argument(
        "--service",
        action="append",
        choices=tuple(LAUNCH_AGENT_FILENAMES),
        required=True,
        help="Service à produire (répéter l'option pour les deux)",
    )
    parser.add_argument("--repo-root", type=Path, default=PROJECT_DIR)
    parser.add_argument(
        "--venv",
        type=Path,
        help="Venv backend (défaut : <repo>/venv, distinct de JARVIS_VENV MLX)",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--supervisor-port", type=int, default=9000)
    parser.add_argument("--imessage-port", type=int, default=8193)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "generate" and args.output_dir is None:
        print("Erreur : --output-dir est obligatoire avec generate", file=sys.stderr)
        return 2
    output_dir = args.output_dir or (Path.home() / "Library" / "LaunchAgents")
    try:
        written = write_launch_agents(
            output_dir=output_dir,
            repo_root=args.repo_root,
            venv_dir=args.venv,
            services=args.service,
            supervisor_port=args.supervisor_port,
            imessage_port=args.imessage_port,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1

    for service, path in written.items():
        print(f"{service}: {path}")
    if args.command == "install":
        print("Plist(s) installé(s). Charge-les avec launchctl bootstrap gui/$(id -u) <plist>.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

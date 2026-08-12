"""CI hermétique de l'intégration, appelée par le runner générique JARVIS."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _run(*args: str) -> None:
    subprocess.run((sys.executable, *args), cwd=ROOT, check=True)


def phase_commands(
    phase: str, *, full_removal: bool = True
) -> tuple[tuple[str, ...], ...]:
    """Retourne les commandes canoniques; removal est toujours la preuve complète."""

    if phase == "offline":
        return (("-m", "pytest", "integrations/opencode/tests", "-q"),)
    if phase == "live":
        return (
            ("-m", "integrations.opencode.scripts.manager", "install"),
            ("-m", "integrations.opencode.scripts.manager", "verify"),
            (
                "-m",
                "pytest",
                "-m",
                "external_network",
                "integrations/opencode/tests/test_real_binary_e2e.py",
                "integrations/opencode/tests/test_adapter_runtime_safety.py",
                "-q",
            ),
        )
    if phase == "removal":
        command = ["-m", "integrations.opencode.tools.removal_proof"]
        if full_removal:
            command.append("--full")
        return (tuple(command),)
    raise ValueError(f"phase CI inconnue: {phase}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase", required=True, choices=("offline", "live", "removal")
    )
    parser.add_argument("--quick-removal", action="store_true")
    args = parser.parse_args()
    if args.quick_removal and args.phase != "removal":
        parser.error("--quick-removal est réservé à la phase removal")
    for command in phase_commands(args.phase, full_removal=not args.quick_removal):
        _run(*command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

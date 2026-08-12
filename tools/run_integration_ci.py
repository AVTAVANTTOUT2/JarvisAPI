#!/usr/bin/env python3
"""Discover and execute opt-in CI hooks declared by removable integrations."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_MODULE_RE = re.compile(r"^integrations\.([a-z][a-z0-9_]*)\.tools\.ci$")
_PHASES = frozenset({"offline", "live", "removal"})


def discover_hooks(root: Path, phase: str) -> list[tuple[str, str]]:
    hooks: list[tuple[str, str]] = []
    integrations = root / "integrations"
    if phase not in _PHASES or not integrations.is_dir():
        return hooks
    for manifest_path in sorted(integrations.glob("*/plugin.json")):
        if manifest_path.is_symlink() or not manifest_path.is_file():
            continue
        payload: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
        ci = payload.get("ci")
        if not isinstance(ci, dict):
            continue
        module = str(ci.get("module") or "")
        phases = ci.get("phases")
        match = _MODULE_RE.fullmatch(module)
        if (
            match is None
            or manifest_path.parent.name != match.group(1)
            or not isinstance(phases, list)
            or any(item not in _PHASES for item in phases)
        ):
            raise ValueError(f"hook CI d'intégration invalide: {manifest_path}")
        if phase in phases:
            hooks.append((manifest_path.parent.name, module))
    return hooks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=sorted(_PHASES))
    parser.add_argument(
        "--quick-removal",
        action="store_true",
        help="Exécuter seulement la preuve rapide; réservé à la phase removal",
    )
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    if args.quick_removal and args.phase != "removal":
        parser.error("--quick-removal est réservé à --phase removal")
    root = args.root.resolve(strict=True)
    for integration_id, module in discover_hooks(root, args.phase):
        print(f"[integration-ci] {integration_id}: {args.phase}", flush=True)
        command = [sys.executable, "-m", module, "--phase", args.phase]
        if args.quick_removal:
            command.append("--quick-removal")
        subprocess.run(
            command,
            cwd=root,
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

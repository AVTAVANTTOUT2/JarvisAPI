#!/usr/bin/env python3
"""Administre le chiffrement SQLCipher des bases de profils JARVIS."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from database.encryption import (  # noqa: E402
    DatabaseEncryptionError,
    database_encryption_status,
    disable_database_encryption,
    enable_database_encryption,
)


def _normalize_profile_id(value: str) -> str:
    from database.core import normalize_profile_id

    return normalize_profile_id(value)


def _profile_paths(*, all_profiles: bool, profile_id: str) -> list[tuple[str, Path]]:
    base = Path(config.DB_PATH)
    if not all_profiles:
        selected = _normalize_profile_id(profile_id)
        if selected == "default":
            return [(selected, base)]
        return [(selected, base.parent / "profiles" / selected / base.name)]

    paths: list[tuple[str, Path]] = [("default", base)]
    profile_root = base.parent / "profiles"
    if profile_root.is_dir():
        for candidate in sorted(profile_root.iterdir()):
            if not candidate.is_dir():
                continue
            try:
                selected = _normalize_profile_id(candidate.name)
            except ValueError:
                continue
            db_path = candidate / base.name
            if db_path.is_file():
                paths.append((selected, db_path))
    return paths


def _run(args: argparse.Namespace) -> int:
    reports: list[dict] = []
    paths = _profile_paths(all_profiles=args.all_profiles, profile_id=args.profile)
    for profile_id, db_path in paths:
        try:
            if args.command == "status":
                report = {
                    "ok": True,
                    "status": database_encryption_status(db_path),
                }
            elif args.command == "enable":
                report = enable_database_encryption(db_path, profile_id)
            else:
                report = disable_database_encryption(db_path, profile_id)
        except (DatabaseEncryptionError, OSError) as exc:
            report = {"ok": False, "error": str(exc)}
        reports.append({"profile_id": profile_id, "path": str(db_path), **report})

    print(json.dumps({"ok": all(item["ok"] for item in reports), "profiles": reports}, indent=2))
    return 0 if all(item["ok"] for item in reports) else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspecte ou migre les bases JARVIS entre SQLite et SQLCipher."
    )
    parser.add_argument("command", choices=("status", "enable", "disable"))
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--profile", default="default")
    selection.add_argument("--all-profiles", action="store_true")
    return _run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

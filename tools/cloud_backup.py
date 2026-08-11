#!/usr/bin/env python3
"""Opérations WebDAV chiffrées sans exposer les credentials."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from scripts.cloud_backup import (  # noqa: E402
    CloudBackupError,
    cloud_backup_status,
    list_cloud_backups,
    render_cloud_report,
    restore_cloud_backup,
    upload_cloud_backup,
)
from scripts.db_maintenance import list_backups  # noqa: E402


def _local_backup(name: str | None) -> Path:
    backups = [item for item in list_backups() if item.get("encrypted")]
    if name:
        match = next((item for item in backups if item["name"] == name), None)
        if match is None:
            raise CloudBackupError("Sauvegarde locale chiffrée introuvable")
    elif backups:
        match = backups[0]
    else:
        raise CloudBackupError("Aucune sauvegarde locale chiffrée disponible")
    return Path(config.BACKUP_DIR) / match["name"]


def _run(args: argparse.Namespace) -> int:
    try:
        if args.command == "status":
            report = cloud_backup_status()
        elif args.command == "list":
            report = {"ok": True, "backups": list_cloud_backups()}
        elif args.command == "upload":
            report = upload_cloud_backup(_local_backup(args.name))
        else:
            if not args.name:
                raise CloudBackupError("Le nom distant est obligatoire pour restore")
            report = restore_cloud_backup(args.name)
    except (CloudBackupError, OSError) as exc:
        report = {"ok": False, "error": str(exc)}
    print(render_cloud_report(report))
    return 0 if report.get("ok", False) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Sauvegardes JARVIS chiffrées sur WebDAV")
    parser.add_argument("command", choices=("status", "list", "upload", "restore"))
    parser.add_argument("name", nargs="?", help="Nom local/distant ; upload choisit le plus récent")
    return _run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

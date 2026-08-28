"""Sondes TCC macOS — non destructives, aucun chemin dans le payload public."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

CheckState = Literal["ok", "denied", "unknown"]


def probe_macos_permissions() -> dict[str, Any]:
    """État fermé pour ``GET /api/integrations``. Linux → runtime absent."""
    if sys.platform != "darwin":
        return {
            "available": False,
            "reason": "optional_runtime_absent",
            "checks": [],
        }
    # ponytail: FDA + AX only. Automation/mic/screen would prompt TCC or
    # leak TCC.db paths; add those probes only if FDA is already ok.
    checks = (
        {"id": "full_disk_access", "state": _full_disk_access_state()},
        {"id": "accessibility", "state": _accessibility_state()},
    )
    return {
        "available": True,
        "reason": "ok",
        "checks": list(checks),
    }


def probe_macos_permissions_safe() -> dict[str, Any]:
    """Ne lève jamais : l'état des intégrations ne doit pas tomber pour une sonde."""
    try:
        return probe_macos_permissions()
    except Exception:  # noqa: BLE001
        return {"available": False, "reason": "status_failed", "checks": []}


def _full_disk_access_state() -> CheckState:
    from integrations.apple_data import DEFAULT_CHAT_DB_PATH

    chat_db = Path(DEFAULT_CHAT_DB_PATH)
    if not chat_db.exists():
        return "unknown"
    try:
        with chat_db.open("rb") as handle:
            handle.read(1)
    except OSError:
        return "denied"
    return "ok"


def _accessibility_state() -> CheckState:
    try:
        from ctypes import c_bool, cdll, util

        library = util.find_library("ApplicationServices")
        if not library:
            return "unknown"
        loaded = cdll.LoadLibrary(library)
        loaded.AXIsProcessTrusted.restype = c_bool
        return "ok" if loaded.AXIsProcessTrusted() else "denied"
    except Exception:
        return "unknown"

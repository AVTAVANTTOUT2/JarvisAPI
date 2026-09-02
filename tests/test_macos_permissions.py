"""Doctor TCC — payload fermé, pas de chemin, Linux = absent, fail-open."""

from __future__ import annotations

import importlib
from pathlib import Path

from integrations import macos_permissions
from integrations.macos_permissions import (
    probe_macos_permissions,
    probe_macos_permissions_safe,
)


def test_probe_on_non_darwin_is_absent(monkeypatch):
    monkeypatch.setattr("integrations.macos_permissions.sys.platform", "linux")
    payload = probe_macos_permissions()
    assert payload["available"] is False
    assert payload["reason"] == "optional_runtime_absent"
    assert payload["checks"] == []
    blob = str(payload)
    assert "/Users" not in blob
    assert "Library" not in blob


def test_probe_safe_delegates_when_probe_succeeds(monkeypatch):
    monkeypatch.setattr("integrations.macos_permissions.sys.platform", "linux")
    assert probe_macos_permissions_safe() == probe_macos_permissions()


def test_probe_safe_swallows_exception_without_raising(monkeypatch):
    """GET /api/integrations ne doit pas tomber si la sonde TCC explose."""

    def boom() -> dict:
        raise RuntimeError("TCC.db unreadable")

    monkeypatch.setattr(macos_permissions, "probe_macos_permissions", boom)
    payload = probe_macos_permissions_safe()
    assert payload == {
        "available": False,
        "reason": "status_failed",
        "checks": [],
    }
    blob = str(payload)
    assert "TCC" not in blob
    assert "unreadable" not in blob


def test_probe_on_darwin_returns_closed_check_states(monkeypatch):
    monkeypatch.setattr("integrations.macos_permissions.sys.platform", "darwin")
    monkeypatch.setattr(
        macos_permissions, "_full_disk_access_state", lambda: "ok"
    )
    monkeypatch.setattr(
        macos_permissions, "_accessibility_state", lambda: "denied"
    )
    payload = probe_macos_permissions()
    assert payload["available"] is True
    assert payload["reason"] == "ok"
    assert payload["checks"] == [
        {"id": "full_disk_access", "state": "ok"},
        {"id": "accessibility", "state": "denied"},
    ]
    blob = str(payload)
    assert "/Users" not in blob
    assert "Library" not in blob
    assert "chat.db" not in blob


def _apple_data_module():
    # ``integrations.apple_data`` côté package est le singleton service ;
    # le module réel (où vit DEFAULT_CHAT_DB_PATH) reste dans sys.modules.
    return importlib.import_module("integrations.apple_data")


def test_full_disk_access_unknown_when_chat_db_missing(monkeypatch, tmp_path: Path):
    missing = tmp_path / "Messages" / "chat.db"
    monkeypatch.setattr(_apple_data_module(), "DEFAULT_CHAT_DB_PATH", missing)
    assert macos_permissions._full_disk_access_state() == "unknown"


def test_full_disk_access_denied_on_oserror(monkeypatch, tmp_path: Path):
    chat_db = tmp_path / "chat.db"
    chat_db.write_bytes(b"x")
    monkeypatch.setattr(_apple_data_module(), "DEFAULT_CHAT_DB_PATH", chat_db)

    real_open = Path.open

    def deny(self, *args, **kwargs):
        if Path(self) == chat_db:
            raise PermissionError("Operation not permitted")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny)
    assert macos_permissions._full_disk_access_state() == "denied"


def test_full_disk_access_ok_when_readable(monkeypatch, tmp_path: Path):
    chat_db = tmp_path / "chat.db"
    chat_db.write_bytes(b"ok")
    monkeypatch.setattr(_apple_data_module(), "DEFAULT_CHAT_DB_PATH", chat_db)
    assert macos_permissions._full_disk_access_state() == "ok"


def test_accessibility_unknown_when_framework_missing(monkeypatch):
    from ctypes import util as ctypes_util

    monkeypatch.setattr(ctypes_util, "find_library", lambda _name: None)
    assert macos_permissions._accessibility_state() == "unknown"


def test_accessibility_unknown_on_load_failure(monkeypatch):
    from ctypes import cdll, util as ctypes_util

    monkeypatch.setattr(ctypes_util, "find_library", lambda _name: "ApplicationServices")

    def boom(_path: str):
        raise OSError("cannot load")

    monkeypatch.setattr(cdll, "LoadLibrary", boom)
    assert macos_permissions._accessibility_state() == "unknown"

"""Doctor TCC — payload fermé, pas de chemin, Linux = absent."""

from __future__ import annotations

from integrations.macos_permissions import probe_macos_permissions


def test_probe_on_non_darwin_is_absent(monkeypatch):
    monkeypatch.setattr("integrations.macos_permissions.sys.platform", "linux")
    payload = probe_macos_permissions()
    assert payload["available"] is False
    assert payload["reason"] == "optional_runtime_absent"
    assert payload["checks"] == []
    blob = str(payload)
    assert "/Users" not in blob
    assert "Library" not in blob

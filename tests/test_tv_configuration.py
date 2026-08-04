"""Contrats fail-closed de la configuration TV locale."""

from __future__ import annotations

import asyncio
from pathlib import Path


def test_tv_defaults_contain_no_personal_infrastructure():
    source = (Path(__file__).resolve().parents[1] / "config.py").read_text(
        encoding="utf-8"
    )

    assert 'TV_IP = _get("TV_IP", "")' in source
    assert 'TV_MAC = _get("TV_MAC", "")' in source
    assert 'TV_DASHBOARD_URL = _get("TV_DASHBOARD_URL", "")' in source
    assert 'TV_CAST_ENABLED = _get("TV_CAST_ENABLED", "false")' in source


def test_tv_action_refuses_adb_without_configured_ip(monkeypatch):
    import config
    from actions import _action_tv

    monkeypatch.setattr(config, "TV_IP", "")

    result = asyncio.run(_action_tv({"command": "home"}))

    assert result["ok"] is False
    assert "TV_IP" in result["message"]


def test_wake_on_lan_refuses_missing_mac(monkeypatch):
    import config
    from actions import _action_tv

    monkeypatch.setattr(config, "TV_IP", "")
    monkeypatch.setattr(config, "TV_MAC", "")

    result = asyncio.run(_action_tv({"command": "wol"}))

    assert result["ok"] is False
    assert "TV_MAC" in result["message"]

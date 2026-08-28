"""Charte de confiance — profils et schémas launch."""

from __future__ import annotations

import config


def test_unknown_profile_falls_back_to_restricted():
    assert config.parse_trust_profile("nope") == "restricted"
    assert config.parse_trust_profile("") == "restricted"
    assert config.parse_trust_profile("MAJORDOMO") == "majordomo"


def test_restricted_does_not_autorun_shortcuts():
    assert config.trust_allows("local.launch", profile="restricted") is True
    assert config.trust_allows("local.shortcuts", profile="restricted") is False
    assert config.trust_allows("local.shortcuts", profile="majordomo") is True
    assert config.trust_allows("comms.send", profile="majordomo") is False


def test_javascript_cannot_enter_launch_schemes():
    assert "javascript" not in config.LAUNCH_URL_SCHEMES
    assert "https" in config.LAUNCH_URL_SCHEMES
    assert "data" not in config.LAUNCH_URL_SCHEMES


def test_shortcuts_capability_confirmation_follows_charter(monkeypatch):
    from jarvis.cognitive.capability_registry import CapabilityRegistry

    monkeypatch.setattr(config, "JARVIS_TRUST_PROFILE", "restricted")
    restricted = CapabilityRegistry().get("apple.shortcuts.run")
    assert restricted is not None
    assert restricted.requires_confirmation is True

    monkeypatch.setattr(config, "JARVIS_TRUST_PROFILE", "majordomo")
    majordomo = CapabilityRegistry().get("apple.shortcuts.run")
    assert majordomo is not None
    assert majordomo.requires_confirmation is False

    launch = CapabilityRegistry().get("computer.launch")
    assert launch is not None
    assert launch.requires_confirmation is False
    assert launch.available is bool(config.COMPUTER_ACCESS)

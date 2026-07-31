"""Resolution du micro d'entree — index explicite, jamais None si un device existe."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


def _make_daemon() -> Any:
    from scripts.audio_daemon import AudioDaemon

    return AudioDaemon()


def test_resolve_prefers_configured_device_name() -> None:
    daemon = _make_daemon()
    import scripts.audio_daemon as mod

    monkey = pytest.MonkeyPatch()
    monkey.setattr(mod.config, "AUDIO_DAEMON_INPUT_DEVICE", "Shiver")
    try:
        pa = MagicMock()
        pa.get_device_count.return_value = 2
        pa.get_device_info_by_index.side_effect = [
            {"name": "Speaker", "maxInputChannels": 0},
            {"name": "Shiver MSS-10", "maxInputChannels": 1},
        ]
        assert daemon._resolve_input_device_index(pa) == 1
    finally:
        monkey.undo()


def test_resolve_falls_back_to_explicit_default_index_when_name_missing() -> None:
    daemon = _make_daemon()
    import scripts.audio_daemon as mod

    monkey = pytest.MonkeyPatch()
    monkey.setattr(mod.config, "AUDIO_DAEMON_INPUT_DEVICE", "Shiver MSS-10")
    try:
        pa = MagicMock()
        pa.get_device_count.return_value = 1
        pa.get_device_info_by_index.return_value = {
            "name": "AirPods Max",
            "maxInputChannels": 1,
        }
        pa.get_default_input_device_info.return_value = {
            "index": 0,
            "name": "AirPods Max",
        }
        assert daemon._resolve_input_device_index(pa) == 0
        pa.get_default_input_device_info.assert_called_once()
    finally:
        monkey.undo()


def test_resolve_empty_config_uses_system_default_not_usb_prefer() -> None:
    """Sans AUDIO_DAEMON_INPUT_DEVICE : défaut macOS, pas d'auto Snowball/Shiver."""
    daemon = _make_daemon()
    import scripts.audio_daemon as mod

    monkey = pytest.MonkeyPatch()
    monkey.setattr(mod.config, "AUDIO_DAEMON_INPUT_DEVICE", "")
    try:
        pa = MagicMock()
        pa.get_device_count.return_value = 2
        pa.get_device_info_by_index.side_effect = [
            {"name": "Shiver MSS-10", "maxInputChannels": 1},
            {"name": "AirPods Max", "maxInputChannels": 1},
        ]
        pa.get_default_input_device_info.return_value = {
            "index": 1,
            "name": "AirPods Max",
        }
        assert daemon._resolve_input_device_index(pa) == 1
        pa.get_default_input_device_info.assert_called_once()
    finally:
        monkey.undo()


def test_resolve_returns_none_only_when_no_input_device() -> None:
    daemon = _make_daemon()
    import scripts.audio_daemon as mod

    monkey = pytest.MonkeyPatch()
    monkey.setattr(mod.config, "AUDIO_DAEMON_INPUT_DEVICE", "")
    try:
        pa = MagicMock()
        pa.get_device_count.return_value = 1
        pa.get_device_info_by_index.return_value = {
            "name": "HDMI",
            "maxInputChannels": 0,
        }
        pa.get_default_input_device_info.side_effect = OSError("no default")
        assert daemon._resolve_input_device_index(pa) is None
    finally:
        monkey.undo()

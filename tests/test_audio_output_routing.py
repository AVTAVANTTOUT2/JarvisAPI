"""Tests routage sortie audio = défaut système (pas d'appariement casque)."""

from __future__ import annotations

from types import SimpleNamespace

from audio.audio_output import resolve_preferred_output_device


class _FakeSD:
    def __init__(self, devices: list[dict], default: tuple[int, int]) -> None:
        self._devices = devices
        self.default = SimpleNamespace(device=default)

    def query_devices(self, idx: int | None = None):
        if idx is None:
            return list(self._devices)
        return self._devices[idx]

    def query_hostapis(self, _i: int = 0):
        return {"default_output_device": self.default.device[1]}


def test_resolve_output_uses_system_default_not_mic_pairing(monkeypatch):
    """Micro AirPods + sortie HP système → on garde la sortie système."""
    monkeypatch.delenv("AUDIO_DAEMON_OUTPUT_DEVICE", raising=False)
    monkeypatch.setattr("config.AUDIO_DAEMON_OUTPUT_DEVICE", "")
    sd = _FakeSD(
        [
            {"name": "PHL", "max_output_channels": 2, "max_input_channels": 0},
            {"name": "AirPods Max de Avity", "max_output_channels": 0, "max_input_channels": 1},
            {"name": "AirPods Max de Avity", "max_output_channels": 1, "max_input_channels": 0},
            {"name": "Haut-parleurs Mac mini", "max_output_channels": 2, "max_input_channels": 0},
        ],
        default=(1, 3),
    )
    assert resolve_preferred_output_device(sd) == 3


def test_resolve_output_override_by_name(monkeypatch):
    monkeypatch.setenv("AUDIO_DAEMON_OUTPUT_DEVICE", "PHL")
    sd = _FakeSD(
        [
            {"name": "PHL 241V8", "max_output_channels": 2, "max_input_channels": 0},
            {"name": "AirPods", "max_output_channels": 1, "max_input_channels": 0},
        ],
        default=(0, 1),
    )
    assert resolve_preferred_output_device(sd) == 0


def test_resolve_output_override_by_index(monkeypatch):
    monkeypatch.setenv("AUDIO_DAEMON_OUTPUT_DEVICE", "1")
    sd = _FakeSD(
        [
            {"name": "A", "max_output_channels": 2, "max_input_channels": 0},
            {"name": "B", "max_output_channels": 2, "max_input_channels": 0},
        ],
        default=(0, 0),
    )
    assert resolve_preferred_output_device(sd) == 1

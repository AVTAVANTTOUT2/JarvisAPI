"""Contrats du chargement Silero VAD sans réseau au runtime."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import Mock

from audio.vad_silero import SileroVAD


def test_vad_loads_the_model_from_the_installed_package(monkeypatch):
    model = Mock()
    loader = Mock(return_value=model)
    package = ModuleType("silero_vad")
    package.load_silero_vad = loader
    monkeypatch.setitem(sys.modules, "silero_vad", package)

    vad = SileroVAD(threshold=0.42)

    assert vad.available is True
    loader.assert_called_once_with()
    model.eval.assert_called_once_with()


def test_vad_falls_back_cleanly_when_packaged_model_fails(monkeypatch):
    package = ModuleType("silero_vad")
    package.load_silero_vad = Mock(side_effect=RuntimeError("invalid local model"))
    monkeypatch.setitem(sys.modules, "silero_vad", package)

    vad = SileroVAD()

    assert vad.available is False
    assert vad.is_speech(b"\x00\x00" * 512) is False

"""Tests du bridge TTSKit : disponibilité MLX et propagation --speaker."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from native_audio import ttskit_bridge as bridge


def test_mlx_python_path_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JARVIS_VENV", str(tmp_path / "no-venv"))
    assert bridge.mlx_python_path() is None


def test_mlx_python_path_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    python = tmp_path / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    monkeypatch.setenv("JARVIS_VENV", str(tmp_path))
    assert bridge.mlx_python_path() == python


def test_is_ttskit_available_false_without_mlx_python(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "ttskit_synthesize"
    launcher.write_text("#!/bin/bash\n")
    launcher.chmod(0o755)
    monkeypatch.setenv("JARVIS_VENV", str(tmp_path / "missing-venv"))
    monkeypatch.setattr(bridge, "_DEFAULT_BINARY", launcher)
    monkeypatch.setattr(bridge, "ttskit_binary", lambda: launcher)
    monkeypatch.setattr(bridge.shutil, "which", lambda _name: None)
    assert bridge.is_ttskit_available() is False


def test_is_ttskit_available_true_with_mlx_python(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "ttskit_synthesize"
    launcher.write_text("#!/bin/bash\n")
    launcher.chmod(0o755)
    python = tmp_path / "mlx" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    monkeypatch.setenv("JARVIS_VENV", str(tmp_path / "mlx"))
    monkeypatch.setattr(bridge, "_DEFAULT_BINARY", launcher)
    monkeypatch.setattr(bridge, "ttskit_binary", lambda: launcher)
    monkeypatch.setattr(bridge.shutil, "which", lambda _name: None)
    assert bridge.is_ttskit_available() is True


def test_is_ttskit_available_external_binary_skips_venv_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    external = tmp_path / "jarvis-ttskit"
    external.write_text("#!/bin/bash\n")
    external.chmod(0o755)
    monkeypatch.setenv("JARVIS_VENV", str(tmp_path / "missing"))
    monkeypatch.setattr(bridge.shutil, "which", lambda _name: str(external))
    monkeypatch.setattr(bridge, "ttskit_binary", lambda: external)
    assert bridge.is_ttskit_available() is True


@pytest.mark.asyncio
async def test_stream_pcm16_passes_speaker(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}

    class _FakeStdout:
        def __init__(self) -> None:
            self._chunks = [b"pcm", b""]

        async def read(self, _n: int) -> bytes:
            return self._chunks.pop(0)

    class _FakeStderr:
        async def read(self) -> bytes:
            return b""

    class _FakeProc:
        def __init__(self) -> None:
            self.stdout = _FakeStdout()
            self.stderr = _FakeStderr()
            self.returncode: int | None = None

        async def wait(self) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            self.returncode = -9

    async def _fake_exec(*cmd: str, **_kwargs):
        captured["cmd"] = list(cmd)
        return _FakeProc()

    monkeypatch.setattr(bridge, "ttskit_binary", lambda: Path("/tmp/ttskit_synthesize"))
    monkeypatch.setattr(bridge.asyncio, "create_subprocess_exec", _fake_exec)

    chunks = [
        chunk
        async for chunk in bridge.stream_pcm16(
            "Bonjour",
            model="qwen3-tts-0.6b",
            language="fr",
            speaker="Aiden",
        )
    ]
    assert chunks == [b"pcm"]
    assert "--speaker" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--speaker") + 1] == "Aiden"


@pytest.mark.asyncio
async def test_ttskit_engine_forwards_speaker_from_config() -> None:
    from audio.tts_native import TTSKitEngine

    seen: dict[str, object] = {}

    async def _fake_stream(text: str, **kwargs):
        seen["text"] = text
        seen["kwargs"] = kwargs
        yield b"x"

    fake_bus = MagicMock()
    fake_bus.emit = AsyncMock()
    engine = TTSKitEngine()
    engine._speaker = "Vivian"
    with (
        patch("native_audio.ttskit_bridge.is_ttskit_available", return_value=True),
        patch("native_audio.ttskit_bridge.stream_pcm16", new=_fake_stream),
        patch("audio.tts_native.event_bus", fake_bus),
    ):
        out = b"".join([c async for c in engine.synthesize_stream("Salut")])

    assert out == b"x"
    assert seen["kwargs"]["speaker"] == "Vivian"

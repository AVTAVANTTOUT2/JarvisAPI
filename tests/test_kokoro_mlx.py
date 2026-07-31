"""Tests unitaires Kokoro MLX (sidecar + découpage, sans GPU)."""

from __future__ import annotations

from pathlib import Path

import pytest

from native_audio.kokoro_mlx import (
    chunk_text_for_kokoro,
    estimate_tokens,
    resolve_model_id,
)


def test_chunk_text_splits_on_sentences_under_limit():
    text = "Bonjour Monsieur. Tous les systèmes sont opérationnels. Prêt."
    out = chunk_text_for_kokoro(text, max_tokens=180)
    assert "\n" not in out or estimate_tokens(out.split("\n")[0]) <= 180
    assert "Bonjour Monsieur" in out
    assert "opérationnels" in out


def test_chunk_text_inserts_newlines_when_over_limit():
    words = " ".join(f"mot{i}" for i in range(50))
    sentences = f"{words}. {words}."
    out = chunk_text_for_kokoro(sentences, max_tokens=20)
    parts = out.split("\n")
    assert len(parts) >= 2
    for part in parts:
        assert estimate_tokens(part) <= 20


def test_chunk_text_empty():
    assert chunk_text_for_kokoro("   ") == ""
    assert chunk_text_for_kokoro("") == ""


def test_resolve_model_id_default_and_path(tmp_path: Path):
    assert resolve_model_id("") == "mlx-community/Kokoro-82M-bf16"
    assert resolve_model_id("mlx-community/Kokoro-82M-bf16") == (
        "mlx-community/Kokoro-82M-bf16"
    )
    local = tmp_path / "model"
    local.mkdir()
    assert resolve_model_id(str(local)) == str(local)


def test_is_kokoro_mlx_available_requires_python(monkeypatch, tmp_path: Path):
    from native_audio import kokoro_bridge

    launcher = tmp_path / "kokoro_synthesize"
    launcher.write_text("#!/bin/bash\n", encoding="utf-8")
    launcher.chmod(0o755)
    monkeypatch.setattr(kokoro_bridge, "_DEFAULT_BINARY", launcher)
    monkeypatch.setattr(kokoro_bridge, "mlx_python_path", lambda: None)
    assert kokoro_bridge.is_kokoro_mlx_available() is False

    python = tmp_path / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/bash\n", encoding="utf-8")
    python.chmod(0o755)
    monkeypatch.setattr(kokoro_bridge, "mlx_python_path", lambda: python)
    assert kokoro_bridge.is_kokoro_mlx_available() is True


@pytest.mark.asyncio
async def test_kokoro_engine_mlx_fallback_on_empty(monkeypatch):
    """Sortie MLX vide → repli macOS (jamais Edge)."""
    from audio.tts import KokoroTTSEngine, macos_tts

    engine = KokoroTTSEngine.__new__(KokoroTTSEngine)
    engine._backend = "mlx"
    engine._voice = "ff_siwis"
    engine._lang = "fr-fr"
    engine._lang_code = "f"
    engine._model = "mlx-community/Kokoro-82M-bf16"
    engine._speed = 0.96
    engine._max_tokens = 180
    engine._kokoro = None
    engine._load_failed = False
    engine.available = True

    async def _empty(_text: str) -> bytes:
        return b""

    monkeypatch.setattr(engine, "_ensure_loaded", lambda: True)
    monkeypatch.setattr(engine, "_synthesize_mlx", _empty)

    called: list[str] = []

    async def _fallback(text: str, emotion: str = "neutral") -> bytes:
        called.append(text)
        return b"RIFF_FAKE"

    monkeypatch.setattr(engine, "_fallback_synthesize", _fallback)
    out = await engine.synthesize("Bonjour")
    assert out == b"RIFF_FAKE"
    assert called == ["Bonjour"]

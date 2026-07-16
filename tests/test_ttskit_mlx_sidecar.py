"""Tests unitaires du sidecar TTSKit MLX (helpers purs, sans chargement modèle)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from native_audio import ttskit_mlx as sidecar  # noqa: E402


def test_resolve_model_alias_default() -> None:
    assert (
        sidecar.resolve_model_id("qwen3-tts-0.6b")
        == "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-6bit"
    )


def test_resolve_model_full_hf_id_passthrough() -> None:
    hf = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-bf16"
    assert sidecar.resolve_model_id(hf) == hf


def test_resolve_model_path_wins(tmp_path: Path) -> None:
    local = tmp_path / "weights"
    local.mkdir()
    assert sidecar.resolve_model_id("qwen3-tts-0.6b", str(local)) == str(local)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("fr", "french"),
        ("FR-FR", "french"),
        ("french", "french"),
        ("en", "english"),
        ("auto", "auto"),
    ],
)
def test_resolve_language(raw: str, expected: str) -> None:
    assert sidecar.resolve_language(raw) == expected


def test_resolve_speaker_prefers_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_SPEAKER", "Aiden")
    assert sidecar.resolve_speaker("Ryan") == "Ryan"


def test_resolve_speaker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_SPEAKER", "Aiden")
    assert sidecar.resolve_speaker(None) == "Aiden"


def test_resolve_speaker_ignores_edge_voice_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TTS_SPEAKER", "fr-FR-HenriNeural")
    assert sidecar.resolve_speaker(None) == "Ryan"


def test_resolve_speaker_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TTS_SPEAKER", raising=False)
    assert sidecar.resolve_speaker("") == "Ryan"


def test_audio_to_pcm16_empty() -> None:
    assert sidecar.audio_to_pcm16([]) == b""


def test_audio_to_pcm16_shape() -> None:
    pcm = sidecar.audio_to_pcm16([0.0, 0.5, -0.5, 1.0])
    assert len(pcm) == 8  # 4 samples * int16

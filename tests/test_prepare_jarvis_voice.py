"""Tests du préparateur de voix ``jarvis-fr`` (analyse + sélection, sans STT)."""

from __future__ import annotations

import math
import wave
from array import array
from pathlib import Path

import pytest

from scripts.prepare_jarvis_voice import (
    ClipCandidate,
    _overlap_ratio,
    _window_metrics,
    extract_clip,
    find_source,
    rank_candidates,
)


def _write_tone_wav(
    path: Path,
    *,
    duration_s: float = 30.0,
    sample_rate: int = 24000,
    speech_start_s: float = 5.0,
    speech_end_s: float = 25.0,
) -> Path:
    """WAV synthétique : silence / parole (sinusoïde) / silence."""
    total = int(duration_s * sample_rate)
    samples = array("h", [0] * total)
    speech_start = int(speech_start_s * sample_rate)
    speech_end = int(speech_end_s * sample_rate)
    for index in range(speech_start, speech_end):
        t = index / sample_rate
        # Amplitude ~30 % du plein échelle — parole claire sans clipping.
        samples[index] = int(10000 * math.sin(2 * math.pi * 180 * t))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())
    return path


def test_rank_candidates_prefers_stable_speech_window(tmp_path: Path) -> None:
    wav = _write_tone_wav(tmp_path / "master.wav")
    with wave.open(str(wav), "rb") as handle:
        rate = handle.getframerate()
        samples = array("h")
        samples.frombytes(handle.readframes(handle.getnframes()))

    metrics = _window_metrics(samples, rate)
    ranked = rank_candidates(metrics, min_s=8.0, target_s=12.0, max_s=16.0)
    assert ranked
    best = ranked[0]
    assert isinstance(best, ClipCandidate)
    assert 8.0 <= best.duration_s <= 16.0
    # La parole synthétique occupe 5–25 s : le meilleur clip doit tomber dedans.
    assert best.start_s >= 4.0
    assert best.end_s <= 26.0
    assert best.speech_ratio >= 0.85


def test_extract_clip_writes_mono_pcm16(tmp_path: Path) -> None:
    wav = _write_tone_wav(tmp_path / "master.wav", duration_s=12.0, speech_start_s=1.0, speech_end_s=11.0)
    with wave.open(str(wav), "rb") as handle:
        rate = handle.getframerate()
        samples = array("h")
        samples.frombytes(handle.readframes(handle.getnframes()))

    candidate = ClipCandidate(
        start_s=2.0,
        end_s=8.0,
        score=1.0,
        speech_ratio=1.0,
        rms=1000.0,
        peak=10000.0,
        clip_ratio=0.0,
        zcr=0.05,
    )
    out = extract_clip(samples, rate, candidate, tmp_path / "reference.wav")
    with wave.open(str(out), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == rate
        assert abs(handle.getnframes() / rate - 6.0) < 0.05


def test_overlap_ratio_detects_heavy_overlap() -> None:
    left = ClipCandidate(0.0, 10.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.05)
    right = ClipCandidate(2.0, 12.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.05)
    assert _overlap_ratio(left, right) == pytest.approx(0.8)
    assert _overlap_ratio(left, ClipCandidate(20.0, 30.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.05)) == 0.0


def test_find_source_honours_explicit_path(tmp_path: Path) -> None:
    wav = _write_tone_wav(tmp_path / "VoixJARVIS_source_clonage_24k.wav", duration_s=2.0, speech_start_s=0.2, speech_end_s=1.8)
    assert find_source(wav) == wav.resolve()


def test_find_source_missing_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scripts.prepare_jarvis_voice.DEFAULT_SOURCE_CANDIDATES",
        (tmp_path / "absent.wav",),
    )
    monkeypatch.setattr(
        "scripts.prepare_jarvis_voice.REPO_ROOT",
        tmp_path,
    )
    monkeypatch.setattr(
        "scripts.prepare_jarvis_voice.Path.home",
        lambda: tmp_path,
    )
    with pytest.raises(FileNotFoundError):
        find_source(None)

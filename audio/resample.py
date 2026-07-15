"""Rééchantillonnage PCM mono 16-bit — une seule passe vers 16 kHz pour le STT."""

from __future__ import annotations

import struct
from typing import Sequence


def pcm16_mono_to_float32(pcm_bytes: bytes) -> list[float]:
    """PCM 16-bit signed little-endian → float32 normalisé [-1, 1]."""
    n = len(pcm_bytes) // 2
    if n == 0:
        return []
    samples = struct.unpack(f"<{n}h", pcm_bytes)
    return [s / 32768.0 for s in samples]


def float32_to_pcm16(samples: Sequence[float]) -> bytes:
    """float32 [-1, 1] → PCM 16-bit signed."""
    out: list[int] = []
    for s in samples:
        clamped = max(-1.0, min(1.0, float(s)))
        out.append(int(clamped * 32767.0) if clamped >= 0 else int(clamped * 32768.0))
    return struct.pack(f"<{len(out)}h", *out)


def resample_pcm16_mono(pcm_bytes: bytes, input_rate: int, target_rate: int = 16000) -> bytes:
    """Rééchantillonne du PCM mono 16-bit vers ``target_rate`` (défaut 16 kHz).

    Utilise une moyenne par fenêtre (qualité suffisante pour STT/VAD).
    """
    if input_rate <= 0 or target_rate <= 0 or not pcm_bytes:
        return b""
    if input_rate == target_rate:
        return pcm_bytes

    samples = pcm16_mono_to_float32(pcm_bytes)
    if not samples:
        return b""

    ratio = input_rate / target_rate
    out_len = max(1, int(len(samples) / ratio))
    resampled: list[float] = []
    for i in range(out_len):
        start = int(i * ratio)
        end = max(start + 1, min(len(samples), int((i + 1) * ratio)))
        window = samples[start:end]
        resampled.append(sum(window) / len(window))

    return float32_to_pcm16(resampled)


__all__ = [
    "float32_to_pcm16",
    "pcm16_mono_to_float32",
    "resample_pcm16_mono",
]

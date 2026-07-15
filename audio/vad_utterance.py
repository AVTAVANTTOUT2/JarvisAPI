"""Collecteur d'énoncés VAD — ring buffer de pré-roll sans accumulation de silence."""

from __future__ import annotations

import math
import struct
from collections import deque
from dataclasses import dataclass, field
from typing import Callable


def chunk_rms(pcm_bytes: bytes) -> float:
    """RMS normalisé d'un chunk PCM 16-bit mono."""
    n = len(pcm_bytes) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"{n}h", pcm_bytes)
    sum_sq = sum(s * s for s in samples)
    return math.sqrt(sum_sq / n) / 32768.0


@dataclass
class VadUtteranceConfig:
    chunk_ms: int = 30
    silence_ms: int = 450
    min_speech_ms: int = 200
    max_utterance_s: float = 30.0
    pre_roll_ms: int = 300
    speech_threshold: float = 0.02
    silero_threshold_on: float = 0.42
    silero_threshold_off: float = 0.28
    use_silero: bool = False


@dataclass
class VadUtteranceCollector:
    """Accumule une utterance sans croissance mémoire pendant l'attente silencieuse."""

    config: VadUtteranceConfig
    is_speech_fn: Callable[[bytes], bool]
    get_speech_prob_fn: Callable[[bytes], float] | None = None

    pre_speech_ring: deque[bytes] = field(default_factory=deque)
    frames: list[bytes] = field(default_factory=list)
    has_speech: bool = False
    speech_active: bool = False
    speech_chunks: int = 0
    silent_chunks: int = 0
    total_chunks: int = 0
    _pre_roll_max: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._pre_roll_max = max(1, int(self.config.pre_roll_ms / self.config.chunk_ms))

    def reset(self) -> None:
        """Réinitialise tous les buffers (après utterance, erreur ou timeout)."""
        self.pre_speech_ring.clear()
        self.frames.clear()
        self.has_speech = False
        self.speech_active = False
        self.speech_chunks = 0
        self.silent_chunks = 0
        self.total_chunks = 0

    @property
    def max_chunks(self) -> int:
        return int(self.config.max_utterance_s * 1000 / self.config.chunk_ms)

    @property
    def silence_chunks_threshold(self) -> int:
        return max(1, int(self.config.silence_ms / self.config.chunk_ms))

    @property
    def min_speech_chunks(self) -> int:
        return max(1, int(self.config.min_speech_ms / self.config.chunk_ms))

    def _push_pre_roll(self, chunk: bytes) -> None:
        self.pre_speech_ring.append(chunk)
        while len(self.pre_speech_ring) > self._pre_roll_max:
            self.pre_speech_ring.popleft()

    def _detect_speech(self, chunk: bytes) -> bool:
        if self.config.use_silero and self.get_speech_prob_fn is not None:
            prob = self.get_speech_prob_fn(chunk)
            if self.speech_active:
                return prob >= self.config.silero_threshold_off
            return prob >= self.config.silero_threshold_on
        return self.is_speech_fn(chunk)

    def ingest(self, chunk: bytes) -> bytes | None:
        """Ingère un chunk PCM. Retourne l'audio complet si fin de phrase détectée."""
        if not chunk:
            return None

        is_speech = self._detect_speech(chunk)

        if not self.has_speech:
            self._push_pre_roll(chunk)
            if not is_speech:
                return None
            self.has_speech = True
            self.speech_active = True
            self.frames = list(self.pre_speech_ring)
            # Le pré-roll est majoritairement silencieux : seule la frame qui
            # vient de franchir le seuil compte comme parole effective.
            self.speech_chunks = 1
            self.silent_chunks = 0
            self.total_chunks = len(self.frames)
            return None

        self.frames.append(chunk)
        self.total_chunks += 1

        if is_speech:
            self.speech_active = True
            self.speech_chunks += 1
            self.silent_chunks = 0
        else:
            self.silent_chunks += 1

        flush_force = self.total_chunks >= self.max_chunks
        end_detected = self.silent_chunks >= self.silence_chunks_threshold

        if end_detected and self.speech_chunks < self.min_speech_chunks:
            # Impulsion trop courte : bruit/claquement, on la jette au lieu de
            # conserver jusqu'au timeout maximal.
            self.reset()
            return None

        if flush_force or end_detected:
            audio = b"".join(self.frames)
            self.reset()
            return audio if audio else None

        return None


__all__ = ["VadUtteranceCollector", "VadUtteranceConfig", "chunk_rms"]

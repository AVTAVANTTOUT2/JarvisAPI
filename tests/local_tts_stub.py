"""Fournisseur TTS local factice, conforme au contrat.

Utilisé par les tests qui exercent le pipeline **autour** de la synthèse
(WebSocket, mobile, cache) sans charger de modèle. Il satisfait réellement
``LocalTTSProvider`` : un test qui passerait avec ce stub mais échouerait avec
un vrai fournisseur signalerait une divergence de contrat, pas un détail de
mock.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from jarvis.audio.tts.base import AudioChunk, ProviderInfo

SAMPLE_RATE = 24000


class StubProvider:
    """Rend un fragment de PCM déterministe par appel."""

    def __init__(self, *, sample_rate: int = SAMPLE_RATE) -> None:
        self.sample_rate = sample_rate
        self.calls: list[str] = []
        self.cancelled: list[str] = []
        self.warmups = 0
        self.closed = False

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            provider="stub",
            backend="stub",
            device="cpu",
            model="stub",
            voice="jarvis",
            streaming="segmented",
            sample_rate=self.sample_rate,
            channels=1,
        )

    async def warmup(self) -> None:
        self.warmups += 1

    async def stream(
        self, text: str, *, request_id: str, utterance_id: str,
    ) -> AsyncIterator[AudioChunk]:
        self.calls.append(text)
        # PCM16 mono : deux octets par échantillon, longueur paire garantie.
        payload = (text.encode("utf-8") + b"\x00")[:64]
        if len(payload) % 2:
            payload += b"\x00"
        yield AudioChunk(data=payload, sample_rate=self.sample_rate, is_final=True)

    async def cancel(self, request_id: str) -> None:
        self.cancelled.append(request_id)

    async def close(self) -> None:
        self.closed = True


__all__ = ["SAMPLE_RATE", "StubProvider"]

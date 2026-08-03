"""Emballage WAV — pour les appelants qui ont besoin d'un fichier, pas d'un flux.

Le chemin temps réel (daemon vocal) consomme du PCM fragment par fragment et ne
passe jamais par ici. Restent les appelants qui doivent livrer un fichier
complet : le web, le mobile, les appareils distants, le cache de phrases
canoniques.

Le WAV est choisi parce qu'il n'exige aucun encodeur : le pipeline reste
entièrement local, sans dépendre d'un codec installé sur la machine. Il est lu
par tous les navigateurs modernes.
"""

from __future__ import annotations

import io
import wave
from collections.abc import AsyncIterator

from jarvis.audio.tts.base import PCM_S16LE, AudioChunk, LocalTTSProvider

WAV_MIME = "audio/wav"


def pcm_to_wav(pcm: bytes, *, sample_rate: int, channels: int = 1) -> bytes:
    """Encapsule du PCM16 mono/stéréo dans un conteneur WAV."""
    if not pcm:
        return b""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(max(1, channels))
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm)
    return buffer.getvalue()


async def collect_pcm(chunks: AsyncIterator[AudioChunk]) -> tuple[bytes, int, int]:
    """Concatène un flux de fragments. Retourne ``(pcm, fréquence, canaux)``."""
    parts: list[bytes] = []
    sample_rate = 0
    channels = 1
    async for chunk in chunks:
        if chunk.sample_format != PCM_S16LE:
            raise ValueError(f"format audio inattendu : {chunk.sample_format}")
        if chunk.data:
            parts.append(chunk.data)
            sample_rate = chunk.sample_rate
            channels = chunk.channels
    return b"".join(parts), sample_rate, channels


async def synthesize_wav(
    provider: LocalTTSProvider,
    text: str,
    *,
    request_id: str,
    utterance_id: str = "",
) -> bytes:
    """Synthèse complète en WAV — pour un appelant qui ne peut pas diffuser.

    Coûte la synthèse entière avant le premier octet : à réserver aux clients
    qui reçoivent un fichier (navigateur, appareil distant), jamais au tour de
    parole local.
    """
    pcm, sample_rate, channels = await collect_pcm(
        provider.stream(text, request_id=request_id, utterance_id=utterance_id or request_id)
    )
    if not pcm:
        return b""
    return pcm_to_wav(
        pcm, sample_rate=sample_rate or provider.info().sample_rate, channels=channels
    )


__all__ = ["WAV_MIME", "collect_pcm", "pcm_to_wav", "synthesize_wav"]

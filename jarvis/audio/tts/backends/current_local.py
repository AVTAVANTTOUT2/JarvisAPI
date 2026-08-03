"""Backend local **transitoire** — garde JARVIS parlant, rien de plus.

Ce module existe pour une seule raison : ne pas rendre l'assistant muet sur une
machine où les poids Fish ne sont pas encore installés. Il n'est jamais choisi
automatiquement — il faut écrire ``TTS_PROVIDER=current_local``. Un repli
silencieux vers un autre moteur ferait entendre à l'utilisateur une voix qu'il
n'a pas choisie sans jamais lui dire pourquoi.

Il pilote le sidecar Kokoro déjà présent dans le dépôt, à travers le même
client générique que Fish. C'est volontaire : le jour où Fish est validé sur la
machine, la suppression est mécanique — ce fichier, ``native_audio/kokoro_*``,
et les variables ``KOKORO_*`` de ``config.py`` partent ensemble, sans toucher
au reste du pipeline.

Les réglages ``KOKORO_*`` sont donc lus **ici et nulle part ailleurs**.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator

from jarvis.audio.tts import events
from jarvis.audio.tts.base import PCM_S16LE, AudioChunk, ProviderInfo
from jarvis.audio.tts.config import TTSSettings
from jarvis.audio.tts.errors import TTSUnavailableError
from jarvis.audio.tts.backends.sidecar import (
    SidecarClient,
    mlx_python,
    sidecar_launcher,
)

logger = logging.getLogger(__name__)

PROVIDER_NAME = "current_local"
BACKEND_NAME = "mlx-audio/kokoro"
LAUNCHER = "kokoro_synthesize"
STREAMING_MODE = "segmented"

# Fréquence fixe du modèle Kokoro — déclarée par le sidecar au démarrage, cette
# valeur ne sert que si la trame prête ne dit rien.
FALLBACK_SAMPLE_RATE = 24000


class CurrentLocalTTSProvider:
    """Moteur local historique derrière l'interface définitive."""

    def __init__(self, settings: TTSSettings) -> None:
        self._settings = settings
        self._client: SidecarClient | None = None
        self._sample_rate = FALLBACK_SAMPLE_RATE
        self._warmup_ms: float | None = None

    # ── Réglages propres au moteur transitoire ──────────────────────────────

    @staticmethod
    def _kokoro(name: str, fallback: object) -> object:
        try:
            import config
        except Exception:  # pragma: no cover - dépend de l'import applicatif
            return fallback
        return getattr(config, name, fallback)

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            provider=PROVIDER_NAME,
            backend=BACKEND_NAME,
            device="mlx",
            model=str(self._kokoro("KOKORO_MODEL", "kokoro")).rsplit("/", 1)[-1],
            voice=str(self._kokoro("KOKORO_VOICE", "")),
            streaming=STREAMING_MODE,
            sample_rate=self._sample_rate,
            channels=self._settings.channels,
            offline=True,
        )

    # ── Cycle de vie ────────────────────────────────────────────────────────

    def _build_client(self) -> SidecarClient:
        launcher = sidecar_launcher(LAUNCHER)
        if launcher is None:
            raise TTSUnavailableError(
                f"sidecar {LAUNCHER} introuvable ou non exécutable"
            )
        python = mlx_python()
        if python is None:
            raise TTSUnavailableError(
                "venv MLX introuvable — JARVIS_VENV doit contenir mlx-audio"
            )
        command = [
            str(launcher),
            "--serve",
            "--model", str(self._kokoro("KOKORO_MODEL", "mlx-community/Kokoro-82M-bf16")),
            "--voice", str(self._kokoro("KOKORO_VOICE", "ff_siwis")),
            "--lang-code", str(self._kokoro("KOKORO_LANG_CODE", "f")),
            "--speed", str(self._kokoro("KOKORO_SPEED", 0.96)),
        ]
        return SidecarClient(
            command,
            env={"JARVIS_VENV": str(python.parent.parent), "HF_HUB_OFFLINE": "1"},
            label=PROVIDER_NAME,
            start_timeout_s=max(120.0, self._settings.timeout_seconds * 10),
            chunk_timeout_s=self._settings.timeout_seconds,
        )

    async def warmup(self) -> None:
        if self._client is not None and self._client.ready:
            return
        started = time.perf_counter()
        events.emit_tts_event(
            events.WARMUP_STARTED, provider=PROVIDER_NAME, backend=BACKEND_NAME,
        )
        client = self._build_client()
        metadata = await client.start()
        declared = int(metadata.get("sample_rate") or 0)
        if declared > 0:
            self._sample_rate = declared
        self._client = client
        self._warmup_ms = (time.perf_counter() - started) * 1000.0
        events.emit_tts_event(
            events.WARMUP_COMPLETED,
            provider=PROVIDER_NAME,
            backend=BACKEND_NAME,
            sample_rate=self._sample_rate,
            warmup_ms=round(self._warmup_ms, 1),
        )

    async def stream(
        self,
        text: str,
        *,
        request_id: str,
        utterance_id: str,
    ) -> AsyncIterator[AudioChunk]:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        await self.warmup()
        client = self._client
        if client is None:  # pragma: no cover - warmup lève avant
            raise TTSUnavailableError("current_local : moteur non initialisé")

        started = time.perf_counter()
        events.emit_tts_event(
            events.SYNTHESIS_STARTED,
            provider=PROVIDER_NAME,
            chars=len(cleaned),
            request_id=request_id,
            utterance_id=utterance_id,
        )

        request = {
            "text": cleaned,
            "voice": str(self._kokoro("KOKORO_VOICE", "ff_siwis")),
            "lang_code": str(self._kokoro("KOKORO_LANG_CODE", "f")),
            "speed": float(self._kokoro("KOKORO_SPEED", 0.96)),
            "max_tokens": int(self._kokoro("KOKORO_MAX_TOKENS", 180)),
            "first_chunk_max_tokens": int(
                self._kokoro("KOKORO_FIRST_CHUNK_MAX_TOKENS", 12)
            ),
        }

        total_bytes = 0
        index = 0
        previous: bytes | None = None
        async for pcm in client.stream(request, request_id=request_id):
            if previous is not None:
                yield self._chunk(previous, is_final=False)
            if index == 0:
                events.emit_tts_event(
                    events.FIRST_CHUNK,
                    provider=PROVIDER_NAME,
                    first_chunk_ms=round((time.perf_counter() - started) * 1000, 1),
                    bytes=len(pcm),
                    sample_rate=self._sample_rate,
                    request_id=request_id,
                    utterance_id=utterance_id,
                )
            previous = pcm
            total_bytes += len(pcm)
            index += 1

        if previous is not None:
            yield self._chunk(previous, is_final=True)

        events.emit_tts_event(
            events.SYNTHESIS_COMPLETED,
            provider=PROVIDER_NAME,
            chars=len(cleaned),
            bytes=total_bytes,
            synthesis_ms=round((time.perf_counter() - started) * 1000, 1),
            sample_rate=self._sample_rate,
            request_id=request_id,
            utterance_id=utterance_id,
        )

    def _chunk(self, data: bytes, *, is_final: bool) -> AudioChunk:
        return AudioChunk(
            data=data,
            sample_rate=self._sample_rate,
            channels=self._settings.channels,
            sample_format=PCM_S16LE,
            is_final=is_final,
        )

    async def cancel(self, request_id: str) -> None:
        client = self._client
        if client is None or not request_id:
            return
        client.cancel(request_id)
        events.emit_tts_event(
            events.CANCELLED, provider=PROVIDER_NAME, request_id=request_id,
        )

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.stop()


__all__ = ["BACKEND_NAME", "PROVIDER_NAME", "CurrentLocalTTSProvider"]

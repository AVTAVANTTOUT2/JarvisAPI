"""Fish Audio S2 Pro, en local, sur Apple Silicon — backend cible.

Ce backend n'appelle **jamais** l'API Fish Audio. Il pilote le modèle
`fish_qwen3_omni` fourni par ``mlx-audio``, exécuté par un sidecar dans le venv
MLX, sur le GPU Metal de la machine. Aucune clé, aucune URL, aucun jeton : ce
qui n'est pas sur le disque n'existe pas pour lui.

Trois réalités qu'il serait malhonnête de maquiller :

- **La diffusion est par segment, pas par jeton.** L'implémentation MLX de Fish
  lève explicitement ``NotImplementedError`` sur son mode ``stream``. JARVIS
  découpe donc le texte (``jarvis.audio.tts.segmenter``) et joue chaque segment
  dès qu'il est synthétisé pendant que le suivant se génère. C'est du streaming
  au sens perçu — le premier son arrive avant la fin de la réponse — mais pas
  du streaming natif du modèle, et ``info().streaming`` le dit.
- **L'annulation prend effet à la frontière d'un segment.** La génération d'un
  segment déjà lancé n'est pas interruptible ; la lecture, elle, s'arrête
  immédiatement. C'est ce que l'utilisateur perçoit comme un barge-in.
- **Le modèle doit être installé.** Aucun téléchargement au démarrage ni au
  runtime : poids absents = erreur explicite avec la commande d'installation.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path

from jarvis.audio.tts import events
from jarvis.audio.tts.base import PCM_S16LE, AudioChunk, ProviderInfo
from jarvis.audio.tts.config import TTSSettings
from jarvis.audio.tts.errors import (
    TTSModelNotFoundError,
    TTSUnavailableError,
    TTSUnsupportedDeviceError,
)
from jarvis.audio.tts.backends.sidecar import (
    SidecarClient,
    mlx_python,
    sidecar_launcher,
)

logger = logging.getLogger(__name__)

PROVIDER_NAME = "fish_local"
BACKEND_NAME = "mlx-audio/fish_qwen3_omni"
LAUNCHER = "fish_synthesize"

# Le modèle rend son audio par lot ; le pipeline lui donne des segments déjà
# courts. Voir le module `segmenter` pour la façon dont ces segments naissent.
STREAMING_MODE = "segmented"

# Accélérateurs acceptés. « cuda » n'est pas une valeur exotique : c'est le
# défaut de l'implémentation de référence de Fish, et l'accepter en silence sur
# un Mac produirait un échec incompréhensible au premier énoncé.
SUPPORTED_DEVICES = frozenset({"auto", "mlx", "metal", "gpu", "cpu"})


class FishLocalTTSProvider:
    """Fournisseur local Fish Audio — modèle chaud, sortie PCM16 mono."""

    def __init__(self, settings: TTSSettings) -> None:
        self._settings = settings
        self._client: SidecarClient | None = None
        self._sample_rate = settings.sample_rate
        self._model_dir: Path | None = None
        self._warmup_ms: float | None = None
        self._voice_cloned = False

    # ── Identité ────────────────────────────────────────────────────────────

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            provider=PROVIDER_NAME,
            backend=BACKEND_NAME,
            device=self._resolved_device(),
            model=self._model_label(),
            voice=self._settings.voice_id,
            streaming=STREAMING_MODE,
            sample_rate=self._sample_rate,
            channels=self._settings.channels,
            offline=True,
        )

    def _resolved_device(self) -> str:
        device = self._settings.device
        return "mlx" if device in {"auto", "metal", "gpu"} else device

    def _model_label(self) -> str:
        """Nom court du modèle — jamais un chemin absolu dans les logs."""
        if self._model_dir is not None:
            parts = self._model_dir.parts
            return parts[-1] if len(parts) < 3 else "/".join(parts[-2:])
        return self._settings.model_path.rsplit("/", 1)[-1]

    # ── Préparation ─────────────────────────────────────────────────────────

    def _check_device(self) -> None:
        device = self._settings.device
        if device not in SUPPORTED_DEVICES:
            raise TTSUnsupportedDeviceError(
                f"TTS_DEVICE={device!r} n'existe pas sur cette machine : "
                f"Fish tourne ici via MLX (Metal). Valeurs acceptées : "
                f"{', '.join(sorted(SUPPORTED_DEVICES))}."
            )

    def _resolve_model(self) -> Path:
        from native_audio.fish_local import FishModelMissing, resolve_local_model_dir

        try:
            return resolve_local_model_dir(self._settings.model_path)
        except FishModelMissing as exc:
            raise TTSModelNotFoundError(str(exc)) from exc

    def _build_client(self) -> SidecarClient:
        launcher = sidecar_launcher(LAUNCHER)
        if launcher is None:
            raise TTSUnavailableError(
                f"sidecar {LAUNCHER} introuvable ou non exécutable "
                f"(native_audio/{LAUNCHER})"
            )
        python = mlx_python()
        if python is None:
            raise TTSUnavailableError(
                "venv MLX introuvable — JARVIS_VENV doit pointer sur un "
                "environnement contenant mlx-audio"
            )

        model_dir = self._resolve_model()
        self._model_dir = model_dir

        command = [str(launcher), "--serve", "--model", str(model_dir)]
        reference = self._settings.reference_audio()
        if reference is not None:
            transcript = self._settings.reference_text()
            if not transcript:
                logger.warning(
                    "[fish_local] %s présent sans transcript — voix par défaut "
                    "utilisée (le clonage a besoin des deux)",
                    reference.name,
                )
            else:
                command += ["--ref-audio", str(reference), "--ref-text", transcript]

        return SidecarClient(
            command,
            env={
                "JARVIS_VENV": str(python.parent.parent),
                "HF_HUB_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
            },
            label=PROVIDER_NAME,
            start_timeout_s=max(120.0, self._settings.timeout_seconds * 10),
            chunk_timeout_s=self._settings.timeout_seconds,
        )

    async def warmup(self) -> None:
        """Charge le modèle hors tour de parole. Idempotent."""
        if self._client is not None and self._client.ready:
            return

        self._check_device()
        started = time.perf_counter()
        events.emit_tts_event(
            events.WARMUP_STARTED,
            provider=PROVIDER_NAME,
            backend=BACKEND_NAME,
            device=self._resolved_device(),
        )

        client = self._build_client()
        try:
            metadata = await client.start()
        except TTSUnavailableError:
            events.emit_tts_event(
                events.FAILED, provider=PROVIDER_NAME, reason="warmup_failed",
            )
            raise

        declared = int(metadata.get("sample_rate") or 0)
        if declared > 0:
            if declared != self._settings.sample_rate:
                logger.info(
                    "[fish_local] fréquence du modèle %d Hz (TTS_SAMPLE_RATE=%d) "
                    "— la valeur du modèle fait foi",
                    declared, self._settings.sample_rate,
                )
            self._sample_rate = declared
        self._voice_cloned = bool(metadata.get("voice_cloned"))
        self._client = client
        self._warmup_ms = (time.perf_counter() - started) * 1000.0

        events.emit_tts_event(
            events.WARMUP_COMPLETED,
            provider=PROVIDER_NAME,
            backend=BACKEND_NAME,
            device=self._resolved_device(),
            model=self._model_label(),
            voice=self._settings.voice_id,
            sample_rate=self._sample_rate,
            warmup_ms=round(self._warmup_ms, 1),
        )

    # ── Synthèse ────────────────────────────────────────────────────────────

    async def stream(
        self,
        text: str,
        *,
        request_id: str,
        utterance_id: str,
    ) -> AsyncIterator[AudioChunk]:
        """Diffuse le PCM16 mono du segment ``text``."""
        cleaned = (text or "").strip()
        if not cleaned:
            return

        await self.warmup()
        client = self._client
        if client is None:  # pragma: no cover - warmup lève avant
            raise TTSUnavailableError("fish_local : moteur non initialisé")

        started = time.perf_counter()
        events.emit_tts_event(
            events.SYNTHESIS_STARTED,
            provider=PROVIDER_NAME,
            backend=BACKEND_NAME,
            chars=len(cleaned),
            request_id=request_id,
            utterance_id=utterance_id,
        )

        total_bytes = 0
        index = 0
        previous: bytes | None = None
        try:
            async for pcm in client.stream(
                {"text": cleaned, "max_tokens": 1024}, request_id=request_id,
            ):
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
        except Exception:
            events.emit_tts_event(
                events.FAILED,
                provider=PROVIDER_NAME,
                reason="synthesis_error",
                request_id=request_id,
                chars=len(cleaned),
            )
            raise

        # Le dernier fragment porte le marqueur de fin : la sortie audio peut
        # fermer son flux sans délai de garde.
        if previous is not None:
            yield self._chunk(previous, is_final=True)

        events.emit_tts_event(
            events.SYNTHESIS_COMPLETED,
            provider=PROVIDER_NAME,
            backend=BACKEND_NAME,
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

    # ── Interruption et arrêt ───────────────────────────────────────────────

    async def cancel(self, request_id: str) -> None:
        client = self._client
        if client is None or not request_id:
            return
        client.cancel(request_id)
        events.emit_tts_event(
            events.CANCELLED,
            provider=PROVIDER_NAME,
            request_id=request_id,
            reason="cancelled",
        )

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.stop()


__all__ = ["BACKEND_NAME", "PROVIDER_NAME", "FishLocalTTSProvider"]

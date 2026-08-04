"""Qwen3-TTS 12 Hz, en local, sur Apple Silicon — moteur vocal de JARVIS.

Ce backend n'appelle **jamais** d'API distante. Il pilote le modèle
``qwen3_tts`` fourni par ``mlx-audio``, exécuté par un sidecar dans le venv
MLX, sur le GPU Metal de la machine. Aucune clé, aucune URL, aucun jeton : ce
qui n'est pas sur le disque n'existe pas pour lui.

Trois propriétés sont visibles depuis l'extérieur :

- **``info().streaming`` vaut ``native``.** Le modèle rend l'audio au fil de la
  génération ; JARVIS n'a pas besoin de découper le texte pour obtenir un
  premier son tôt. Le segmenteur reste en place et reste utile, mais il n'est
  plus la seule source de fragments, et l'annonce faite au reste du système
  décrit ce qui se passe réellement.
- **La fréquence native est 24 kHz**, celle du pipeline et du profil vocal :
  aucune conversion, ni en sortie ni sur la référence.
- **Le transcript de la voix ne transite pas par la ligne de commande.** Le
  sidecar reçoit le répertoire du profil et lit lui-même ``transcript.txt`` ;
  le passer en argument l'exposerait dans la sortie de ``ps``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path

from jarvis.audio.tts import events
from jarvis.audio.tts.base import PCM_S16LE, AudioChunk, ProviderInfo
from jarvis.audio.tts.config import VOICE_REFERENCE_AUDIO, TTSSettings
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

PROVIDER_NAME = "qwen3_local"
BACKEND_NAME = "mlx-audio/qwen3_tts"
LAUNCHER = "qwen3_synthesize"

# Le modèle diffuse l'audio pendant qu'il le génère : ce n'est pas le découpage
# du texte par JARVIS qui produit les fragments.
STREAMING_MODE = "native"

# Accélérateurs acceptés. « cuda » n'est pas une valeur exotique : c'est le
# défaut de plusieurs implémentations de référence, et l'accepter en silence
# sur un Mac produirait un échec incompréhensible au premier énoncé.
SUPPORTED_DEVICES = frozenset({"auto", "mlx", "metal", "gpu", "cpu"})

# Le modèle nomme ses langues en toutes lettres, JARVIS les code sur deux
# lettres. Sans cette table, ``LANGUAGE=fr`` serait transmis tel quel, ne
# figurerait pas dans ``codec_language_id``, et le conditionnement de langue
# disparaîtrait **sans aucun avertissement** — le modèle devinerait la langue
# à partir du texte, ce qui marche souvent et rate sur les phrases courtes.
LANGUAGE_CODES: dict[str, str] = {
    "fr": "french",
    "en": "english",
    "de": "german",
    "es": "spanish",
    "it": "italian",
    "pt": "portuguese",
    "ru": "russian",
    "ja": "japanese",
    "ko": "korean",
    "zh": "chinese",
}
DEFAULT_LANGUAGE = "french"


def _model_language() -> str:
    """Langue à transmettre au modèle, dérivée de ``config.LANGUAGE``."""
    try:
        import config as app_config

        raw = str(getattr(app_config, "LANGUAGE", "") or "").strip().lower()
    except Exception:  # pragma: no cover - dépend de l'environnement d'import
        raw = ""
    if not raw:
        return DEFAULT_LANGUAGE
    # Un réglage déjà écrit en toutes lettres (« french ») passe tel quel ; le
    # sidecar valide de toute façon contre la table du modèle.
    return LANGUAGE_CODES.get(raw[:2], raw)


class Qwen3LocalTTSProvider:
    """Fournisseur local Qwen3-TTS — modèle chaud, sortie PCM16 mono 24 kHz."""

    def __init__(self, settings: TTSSettings) -> None:
        self._settings = settings
        self._client: SidecarClient | None = None
        self._sample_rate = settings.sample_rate
        self._warmup_ms: float | None = None
        self._voice_cloned = False
        # Sans ce verrou, deux warmups concurrents (daemon + cache spéculatif)
        # construisent chacun un SidecarClient et chargent le modèle deux fois
        # en mémoire Metal.
        self._warmup_lock = asyncio.Lock()

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
        return self._settings.model_path.rstrip("/").rsplit("/", 1)[-1]

    # ── Préparation ─────────────────────────────────────────────────────────

    def _check_device(self) -> None:
        device = self._settings.device
        if device not in SUPPORTED_DEVICES:
            raise TTSUnsupportedDeviceError(
                f"TTS_DEVICE={device!r} n'existe pas sur cette machine : "
                f"Qwen3-TTS tourne ici via MLX (Metal). Valeurs acceptées : "
                f"{', '.join(sorted(SUPPORTED_DEVICES))}."
            )

    def _resolve_model(self) -> Path:
        from native_audio.qwen3_local import Qwen3ModelMissing, resolve_model_dir

        try:
            return resolve_model_dir(self._settings.model_path)
        except Qwen3ModelMissing as exc:
            raise TTSModelNotFoundError(str(exc)) from exc

    def _build_client(self) -> SidecarClient:
        """Construit le client du sidecar — sans le démarrer."""
        # Les poids d'abord. Sur une machine neuve, ni les poids ni le venv MLX
        # ne sont installés : les deux causes sont vraies, mais une seule est
        # utile. Les poids sont ce que l'utilisateur installe en premier, et
        # `TTSModelNotFoundError` porte la commande exacte. Vérifier le runtime
        # avant masquerait ce diagnostic derrière une erreur plus vague — la CI
        # macOS a déjà échoué exactement là.
        model_dir = self._resolve_model()

        launcher = sidecar_launcher(LAUNCHER)
        if launcher is None:
            raise TTSUnavailableError(
                f"sidecar {LAUNCHER} introuvable ou non exécutable "
                f"(native_audio/{LAUNCHER})"
            )

        python = mlx_python()
        if python is None:
            raise TTSUnavailableError(
                "venv MLX introuvable (JARVIS_VENV, défaut ~/mlx-env). "
                "Installez mlx-audio : "
                "python -m pip install -r requirements-mlx.txt"
            )

        command = [
            str(launcher), "--serve", "--model", str(model_dir),
            "--language", _model_language(),
            "--streaming-interval", str(self._settings.streaming_interval),
            "--clone-mode", self._settings.clone_mode,
        ]

        # Le répertoire du profil suffit : le sidecar y lit lui-même
        # reference.wav et transcript.txt. Passer le transcript en argument
        # l'exposerait dans la sortie de `ps`.
        if self._settings.reference_audio() is not None:
            if not self._settings.reference_text():
                logger.warning(
                    "[qwen3_local] référence présente sans transcript — voix par "
                    "défaut utilisée (le clonage a besoin des deux)",
                )
            command += ["--voice-dir", str(self._settings.voice_dir)]
        else:
            self._warn_if_another_profile_holds_the_voice()

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

    def _warn_if_another_profile_holds_the_voice(self) -> None:
        """Dit à voix haute qu'une voix clonée existe mais n'est plus lue.

        Les échantillons n'étant jamais versionnés, une installation existante
        peut garder son ``reference.wav`` dans un autre répertoire de profil :
        sans ce message, JARVIS repartirait sur la voix par défaut du modèle
        sans que rien ne le signale — exactement le repli silencieux que
        l'architecture interdit.
        """
        voice_dir = self._settings.voice_dir
        parent = voice_dir.parent
        if not parent.is_dir():
            return
        try:
            siblings = sorted(p for p in parent.iterdir() if p.is_dir())
        except OSError:
            return
        for sibling in siblings:
            if sibling == voice_dir:
                continue
            if not (sibling / VOICE_REFERENCE_AUDIO).is_file():
                continue
            logger.warning(
                "[qwen3_local] %s ne contient aucun échantillon alors que %s en "
                "porte un : JARVIS parle avec la voix par défaut du modèle. "
                "Régénérez le profil courant "
                "(python scripts/prepare_jarvis_voice.py) ou pointez "
                "TTS_VOICE_PATH sur %s.",
                voice_dir,
                sibling,
                sibling,
            )
            return

    async def warmup(self) -> None:
        """Charge le modèle hors tour de parole. Idempotent et sérialisé."""
        if self._client is not None and self._client.ready:
            return

        async with self._warmup_lock:
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

            # Remplace un client mort ou partiel avant d'en ouvrir un neuf —
            # sinon un restart laisse un sidecar orphelin en mémoire Metal.
            previous = self._client
            self._client = None
            if previous is not None:
                await previous.stop()

            client = self._build_client()
            try:
                metadata = await client.start()
            except TTSUnavailableError:
                await client.stop()
                events.emit_tts_event(
                    events.FAILED, provider=PROVIDER_NAME, reason="warmup_failed",
                )
                raise

            declared = int(metadata.get("sample_rate") or 0)
            if declared > 0:
                if declared != self._settings.sample_rate:
                    logger.info(
                        "[qwen3_local] fréquence du modèle %d Hz (TTS_SAMPLE_RATE=%d) "
                        "— la valeur du modèle fait foi",
                        declared, self._settings.sample_rate,
                    )
                self._sample_rate = declared
            self._voice_cloned = bool(metadata.get("voice_cloned"))
            self._client = client
            self._warmup_ms = (time.perf_counter() - started) * 1000.0

            if not self._voice_cloned:
                logger.warning(
                    "[qwen3_local] moteur prêt sans voix clonée — JARVIS parlera "
                    "avec la voix par défaut du modèle."
                )
            else:
                # `voice_cloned: true` ne disait pas *comment* la voix est
                # reproduite. Ces quatre valeurs déterminent le timbre obtenu
                # et doivent apparaître dans les journaux de démarrage.
                logger.info(
                    "[qwen3_local] Qwen3 voice ready — voice=%s clone_mode=%s "
                    "reference_duration_ms=%s reference_text_used=%s "
                    "language=%s streaming=%s",
                    self._settings.voice_id,
                    metadata.get("clone_mode"),
                    metadata.get("reference_duration_ms"),
                    "true" if metadata.get("reference_text_used") else "false",
                    metadata.get("language"),
                    metadata.get("streaming"),
                )

            events.emit_tts_event(
                events.WARMUP_COMPLETED,
                provider=PROVIDER_NAME,
                backend=BACKEND_NAME,
                device=self._resolved_device(),
                warmup_ms=round(self._warmup_ms, 1),
                sample_rate=self._sample_rate,
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
            raise TTSUnavailableError("qwen3_local : moteur non initialisé")

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
                {"text": cleaned}, request_id=request_id,
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


__all__ = ["BACKEND_NAME", "PROVIDER_NAME", "Qwen3LocalTTSProvider"]

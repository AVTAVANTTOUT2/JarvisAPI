"""Silero VAD — detection parole/silence neurale.

Beaucoup plus precis que le seuil RMS :
- Distingue parole de bruit ambiant (ventilo, clavier, rue)
- Gere les pauses intra-phrase (respiration, hesitation)
- Detecte le debut et la fin de parole avec precision
- Supporte le downsampling automatique 48kHz → 16kHz (le daemon audio
  peut tourner a 48kHz pour le STT, Silero tourne a 16kHz)

Usage :
    from audio.vad_silero import SileroVAD
    vad = SileroVAD()
    is_speech = vad.is_speech(audio_chunk_bytes)  # True/False
"""

from __future__ import annotations

import logging
import os
import struct
from typing import Optional

logger = logging.getLogger("silero_vad")

# Silero VAD fonctionne a 8kHz ou 16kHz.
# Le pipeline JARVIS peut etre a 16kHz ou 48kHz (config AUDIO_DAEMON_SAMPLE_RATE).
# Si le daemon tourne a 48kHz, on downsample a la volee (1 sample/3).
SILERO_SAMPLE_RATE = 16000
# Silero VAD attend des chunks de 512 samples (32ms) a 16kHz.
# Le daemon produit des chunks de tailles variables selon le sample rate.
# On accumule un buffer interne et on downsampl si necessaire.
SILERO_CHUNK_SIZE = 512
# Fenetre glissante max — on garde ~2s d'audio (16kHz, 16-bit)
BUFFER_MAX_BYTES_16K = SILERO_SAMPLE_RATE * 2 * 2  # 64000 bytes


class SileroVAD:
    """Wrapper Silero VAD pour le daemon audio JARVIS.

    Charge le modele neural Silero (~1 Mo) via torch.hub. Si le chargement
    echoue (torch absent, reseau indisponible), le VAD reste indisponible
    et le daemon doit utiliser le fallback RMS.

    Le modele tourne en <1ms par chunk sur Apple Silicon M4.

    Supporte l'audio en 16kHz ou 48kHz (downsampling automatique
    quand input_sr=48000).
    """

    def __init__(self, threshold: float = 0.5, input_sr: int = 16000):
        """
        Args:
            threshold: seuil de probabilite de parole (0.0-1.0).
                       0.5 = defaut Silero. Plus bas = plus sensible.
            input_sr: sample rate de l'audio entrant (16000 ou 48000).
                     Si 48000, les frames sont downsamplees a 16kHz.
        """
        self.threshold: float = threshold
        self._model: Optional[object] = None
        self._available: bool = False
        self._buffer: bytes = b""  # accumulation (toujours a 16kHz apres downsampling)
        self._input_sr: int = input_sr
        self._downsample: bool = (input_sr == 48000)
        if self._downsample:
            logger.info("[silero_vad] Downsampling active : %d Hz → %d Hz (ratio 1:3)",
                        input_sr, SILERO_SAMPLE_RATE)

        self._load_model()

    def _load_model(self) -> None:
        """Charge le modele Silero VAD via torch.hub (lazy, une fois)."""
        try:
            import torch  # noqa: F401
        except ImportError:
            logger.warning(
                "[silero_vad] torch non installe — pip install torch. Fallback RMS actif."
            )
            return

        try:
            self._model, _ = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                trust_repo=True,
            )
            self._model.eval()
            self._available = True
            logger.info("[silero_vad] Modele charge (threshold=%.2f)", self.threshold)
        except Exception as e:
            logger.warning("[silero_vad] Chargement echoue : %s — fallback RMS", e)
            self._available = False

    @property
    def available(self) -> bool:
        """True si le modele Silero est charge et pret a l'usage."""
        return self._available

    def _downsample_48k_to_16k(self, pcm_bytes: bytes) -> bytes:
        """Downsample PCM 16-bit 48kHz → 16kHz (ratio 1:3, nearest-neighbor).

        Garde 1 sample sur 3. Simple, rapide, sans artefact audible
        pour du VAD (on ne fait pas de reconstruction audio).
        """
        if not self._downsample:
            return pcm_bytes

        # Nombre de samples 16-bit dans le chunk entrant
        total_samples = len(pcm_bytes) // 2
        if total_samples < 3:
            return b""  # pas assez pour downsampler

        # Decoder tous les samples
        all_samples = struct.unpack(f"{total_samples}h", pcm_bytes)

        # Garder 1 sample sur 3
        downsampled = all_samples[0::3]

        return struct.pack(f"{len(downsampled)}h", *downsampled)

    def is_speech(self, pcm_bytes: bytes) -> bool:
        """Detecte si le chunk audio contient de la parole.

        Args:
            pcm_bytes: audio PCM 16-bit signed, 16kHz ou 48kHz mono

        Returns:
            True si parole detectee, False sinon (ou si Silero indisponible)
        """
        if not self._available:
            return False

        # Downsampling si necessaire (48kHz → 16kHz)
        audio_16k = self._downsample_48k_to_16k(pcm_bytes)
        if not audio_16k:
            return False

        # Accumuler les bytes dans le buffer interne (fenetre glissante)
        self._buffer += audio_16k

        # Fenetre glissante : ne garder que les ~2 dernieres secondes
        if len(self._buffer) > BUFFER_MAX_BYTES_16K:
            overflow = len(self._buffer) - BUFFER_MAX_BYTES_16K
            # Avancer d'un multiple de 2 bytes (16-bit aligne)
            overflow = (overflow // 2) * 2
            self._buffer = self._buffer[overflow:]

        # Pas assez de donnees pour un chunk Silero (512 samples x 2 bytes)
        if len(self._buffer) < SILERO_CHUNK_SIZE * 2:
            return False

        # Extraire exactement SILERO_CHUNK_SIZE samples
        chunk_bytes = self._buffer[: SILERO_CHUNK_SIZE * 2]
        self._buffer = self._buffer[SILERO_CHUNK_SIZE * 2 :]

        try:
            import torch

            # Convertir PCM 16-bit signed → float32 tensor normalise [-1, 1]
            samples = struct.unpack(f"{SILERO_CHUNK_SIZE}h", chunk_bytes)
            tensor = torch.FloatTensor(samples) / 32768.0

            # Inference Silero (1 appel, <1ms)
            prob = self._model(tensor, SILERO_SAMPLE_RATE).item()
            return prob >= self.threshold

        except Exception as e:
            logger.debug("[silero_vad] Erreur inference : %s", e)
            return False

    def get_probability(self, pcm_bytes: bytes) -> float:
        """Retourne la probabilite de parole (0.0-1.0) pour le debug.

        Retourne 0.0 si Silero indisponible ou buffer insuffisant.
        """
        if not self._available:
            return 0.0

        audio_16k = self._downsample_48k_to_16k(pcm_bytes)
        if not audio_16k:
            return 0.0

        self._buffer += audio_16k

        if len(self._buffer) < SILERO_CHUNK_SIZE * 2:
            return 0.0

        # Fenetre glissante
        if len(self._buffer) > BUFFER_MAX_BYTES_16K:
            overflow = (len(self._buffer) - BUFFER_MAX_BYTES_16K) // 2 * 2
            self._buffer = self._buffer[overflow:]

        chunk_bytes = self._buffer[: SILERO_CHUNK_SIZE * 2]
        self._buffer = self._buffer[SILERO_CHUNK_SIZE * 2 :]

        try:
            import torch

            samples = struct.unpack(f"{SILERO_CHUNK_SIZE}h", chunk_bytes)
            tensor = torch.FloatTensor(samples) / 32768.0
            prob = self._model(tensor, SILERO_SAMPLE_RATE).item()
            return float(prob)
        except Exception:
            return 0.0

    def reset(self) -> None:
        """Reset l'etat interne du modele et le buffer d'accumulation.

        A appeler entre chaque phrase et apres chaque TTS pour eviter
        que l'etat recurrent du modele ne derive d'une phrase a l'autre.
        """
        self._buffer = b""
        if self._available and self._model is not None:
            try:
                self._model.reset_states()
            except Exception:
                pass

    def reset_buffer(self) -> None:
        """Vide le buffer d'accumulation sans reset du modele.

        Utile apres une purge de queue audio (post-TTS) ou
        un changement d'etat qui rend les frames accumulees obsoletes.
        """
        self._buffer = b""


# ── Singleton ─────────────────────────────────────────────────────────────────

def _build_silero_vad() -> SileroVAD:
    try:
        import config as _cfg

        input_sr = int(getattr(_cfg, "AUDIO_DAEMON_SAMPLE_RATE", 16000))
        threshold = float(getattr(_cfg, "SILERO_VAD_THRESHOLD", 0.42))
    except Exception:
        input_sr = int(os.getenv("AUDIO_DAEMON_SAMPLE_RATE", "16000"))
        threshold = float(os.getenv("SILERO_VAD_THRESHOLD", "0.42"))
    return SileroVAD(threshold=threshold, input_sr=input_sr)


silero_vad = _build_silero_vad()

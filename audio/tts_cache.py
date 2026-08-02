"""Caches TTS — rejeu instantané (« répète ») et synthèse spéculative.

Deux caches process-wide, partagés entre le daemon audio et le WebSocket :

- ``last_tts`` : le dernier audio prononcé par JARVIS (texte + bytes + mime).
  « Jarvis, répète » le rejoue tel quel — zéro STT→LLM→TTS, zéro coût.
- ``speculative_tts`` : audio pré-généré pour les réponses probables
  (accusés de réception, confirmations d'action). Quand la réponse finale
  correspond à une phrase pré-générée, la lecture est instantanée.

Le cache spéculatif est invalidé si le moteur ou la voix TTS change.
"""

from __future__ import annotations

import logging
import unicodedata

import config
from jarvis.audio.tts.config import load_tts_settings

logger = logging.getLogger(__name__)

# Phrases quasi certaines d'être prononcées : pré-générées au démarrage du
# daemon, et re-servies instantanément quand la réponse finale correspond.
CANNED_PHRASES: list[tuple[str, str]] = [
    ("Bien, Monsieur.", "neutral"),
    ("C'est fait, Monsieur.", "neutral"),
    ("Très bien, j'annule.", "neutral"),
    ("Un instant, Monsieur.", "neutral"),
    ("Je vous écoute.", "neutral"),
    ("Oui Monsieur ?", "neutral"),
    ("Bien Monsieur, je reste en veille.", "warm"),
    ("Me revoici, Monsieur.", "warm"),
    ("Je n'ai encore rien dit, Monsieur.", "amused"),
]

_REPEAT_TRIGGERS = (
    "repete",
    "tu peux repeter",
    "peux-tu repeter",
    "redis",
    "qu'est-ce que tu as dit",
    "qu'est ce que tu as dit",
    "j'ai pas entendu",
    "je n'ai pas entendu",
)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(text.split()).strip(" .!?")


def is_repeat_request(text: str) -> bool:
    """True si le message demande de rejouer la dernière réponse."""
    norm = _normalize(text)
    if not norm or len(norm.split()) > 8:
        return False
    return any(t in norm for t in _REPEAT_TRIGGERS)


class LastTTS:
    """Dernier audio TTS prononcé — rejouable sans re-génération."""

    def __init__(self) -> None:
        self._entry: dict | None = None

    def store(self, text: str, emotion: str, audio: bytes, mime: str = "audio/wav") -> None:
        if not text or not audio:
            return
        self._entry = {"text": text, "emotion": emotion, "audio": audio, "mime": mime}

    def get(self) -> dict | None:
        return self._entry


class SpeculativeTTS:
    """Audio pré-généré pour les réponses probables. Clé : (texte normalisé, émotion)."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], bytes] = {}
        self._engine_sig: str | None = None

    @staticmethod
    def _current_sig() -> str:
        """Empreinte fournisseur+voix : un changement de voix vide le cache.

        Lue dans la configuration et non auprès d'un moteur chargé : le cache
        reste générique et son invalidation identique sur toutes les machines,
        y compris là où aucun modèle n'est installé.
        """
        settings = load_tts_settings()
        return f"{settings.provider}:{settings.voice_id}"

    def _check_sig(self) -> None:
        sig = self._current_sig()
        if self._engine_sig != sig:
            if self._engine_sig is not None:
                logger.info("[tts_cache] moteur/voix changé — cache spéculatif vidé")
            self._cache.clear()
            self._engine_sig = sig

    def get(self, text: str, emotion: str = "neutral") -> bytes | None:
        if not getattr(config, "SPECULATIVE_TTS_ENABLED", True):
            return None
        self._check_sig()
        return self._cache.get((_normalize(text), emotion))

    async def put(self, text: str, emotion: str, provider) -> bool:
        """Pré-synthétise et met en cache. Idempotent, jamais bloquant pour l'appelant."""
        if not getattr(config, "SPECULATIVE_TTS_ENABLED", True):
            return False
        self._check_sig()
        key = (_normalize(text), emotion)
        if key in self._cache:
            return True
        try:
            from jarvis.audio.tts.wav import synthesize_wav

            audio = await synthesize_wav(
                provider, text, request_id=f"speculative:{key[0][:24]}",
            )
        except Exception as e:
            logger.debug("[tts_cache] pré-synthèse échouée (%s) : %s", text[:30], e)
            return False
        if audio:
            self._cache[key] = audio
            return True
        return False

    async def warmup(self) -> int:
        """Pré-génère les phrases canoniques (démarrage daemon). Retourne le nb en cache.

        Le fournisseur est celui, unique, du processus : pré-générer avec un
        second modèle chargé en mémoire coûterait plus que le gain.
        """
        if not getattr(config, "SPECULATIVE_TTS_ENABLED", True):
            return 0
        try:
            from jarvis.audio.tts import get_local_tts_provider

            provider = get_local_tts_provider()
            await provider.warmup()
        except Exception as e:
            logger.debug("[tts_cache] warmup : moteur TTS indisponible (%s)", e)
            return 0
        ok = 0
        for text, emotion in CANNED_PHRASES:
            if await self.put(text, emotion, provider):
                ok += 1
        logger.info("[tts_cache] warmup : %d/%d phrases pré-générées", ok, len(CANNED_PHRASES))
        return ok

    def stats(self) -> dict:
        return {"entries": len(self._cache), "engine": self._engine_sig}


last_tts = LastTTS()
speculative_tts = SpeculativeTTS()

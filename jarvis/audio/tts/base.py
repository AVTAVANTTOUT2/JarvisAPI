"""Contrat de synthèse vocale locale — la seule chose que le reste connaît.

Le pipeline vocal (daemon, file de parole, sortie CoreAudio, API mobile) ne
nomme aucun moteur : il crée un fournisseur par la fabrique et consomme des
``AudioChunk``. Changer de backend — Qwen3-TTS local aujourd'hui, autre chose
demain — revient à écrire un module qui satisfait ce protocole.

Trois propriétés de ce contrat sont structurantes :

- **Le fragment porte son format.** Un flux PCM sans fréquence déclarée oblige
  l'appelant à deviner, et une erreur de fréquence produit une voix déformée
  sans jamais lever d'exception. ``AudioChunk`` transporte donc ce qu'il faut
  pour ouvrir le flux de lecture, fragment par fragment.
- **L'annulation est corrélée.** ``cancel(request_id)`` ne peut pas arrêter la
  réponse suivante : une annulation tardive qui couperait la parole en cours
  serait indiscernable d'un bug de synthèse.
- **Le préchargement est explicite.** ``warmup()`` sort le chargement du modèle
  du tour de parole. Un fournisseur qui charge à la première phrase fait payer
  plusieurs secondes à l'utilisateur, une seule fois, au pire moment.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Le pipeline local travaille en PCM16 mono : c'est ce que la sortie CoreAudio
# consomme sans transcodage, donc sans fichier temporaire ni latence ajoutée.
PCM_S16LE = "pcm_s16le"

DEFAULT_SAMPLE_RATE = 24000
DEFAULT_CHANNELS = 1


@dataclass(frozen=True)
class AudioChunk:
    """Fragment audio prêt à jouer, auto-descriptif.

    ``is_final`` marque le dernier fragment d'un énoncé : la sortie audio peut
    fermer son flux sans attendre un délai de garde.
    """

    data: bytes
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_CHANNELS
    sample_format: str = PCM_S16LE
    is_final: bool = False

    @property
    def duration_ms(self) -> float:
        """Durée du fragment — utile aux métriques, jamais au contenu."""
        if self.sample_format != PCM_S16LE or self.sample_rate <= 0:
            return 0.0
        frames = len(self.data) / (2 * max(1, self.channels))
        return frames * 1000.0 / self.sample_rate


@dataclass(frozen=True)
class ProviderInfo:
    """Identité du fournisseur actif, telle qu'affichée dans les diagnostics.

    ``streaming`` distingue deux réalités qu'il serait malhonnête de confondre :
    ``native`` = le modèle rend l'audio au fil de la génération ;
    ``segmented`` = JARVIS découpe le texte et joue chaque phrase dès qu'elle
    est synthétisée, pendant que la suivante se génère.
    """

    provider: str
    backend: str
    device: str
    model: str
    voice: str
    streaming: str
    sample_rate: int
    channels: int
    offline: bool = True

    def as_log_fields(self) -> dict[str, str | int | bool]:
        """Champs journalisables — aucun chemin absolu, aucun secret."""
        return {
            "provider": self.provider,
            "backend": self.backend,
            "device": self.device,
            "model": self.model,
            "voice": self.voice,
            "streaming": self.streaming,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "offline": self.offline,
        }


@runtime_checkable
class LocalTTSProvider(Protocol):
    """Fournisseur de synthèse vocale **local**, sans aucun appel réseau."""

    def info(self) -> ProviderInfo:
        """Identité du moteur : nom, modèle, voix, mode de diffusion."""
        ...

    async def warmup(self) -> None:
        """Charge le modèle hors tour de parole.

        Lève ``TTSModelNotFoundError`` si les poids manquent,
        ``TTSUnavailableError`` si le runtime local est absent.
        """
        ...

    def stream(
        self,
        text: str,
        *,
        request_id: str,
        utterance_id: str,
    ) -> AsyncIterator[AudioChunk]:
        """Diffuse l'audio de ``text``, fragment par fragment.

        Renvoie un itérateur asynchrone : la lecture démarre sur le premier
        fragment. ``request_id`` identifie cette synthèse pour ``cancel``.
        """
        ...

    async def cancel(self, request_id: str) -> None:
        """Interrompt la synthèse ``request_id`` si elle est encore active.

        Une annulation qui ne correspond à aucune requête en cours est sans
        effet — jamais une erreur, jamais l'arrêt de la requête suivante.
        """
        ...

    async def close(self) -> None:
        """Libère le modèle et les processus tenus entre deux énoncés."""
        ...


__all__ = [
    "DEFAULT_CHANNELS",
    "DEFAULT_SAMPLE_RATE",
    "PCM_S16LE",
    "AudioChunk",
    "LocalTTSProvider",
    "ProviderInfo",
]

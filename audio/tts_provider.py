"""Frontière entre le pipeline vocal et le moteur de synthèse.

Le daemon audio, le VAD, le STT, l'orchestration et le lecteur PCM ne
connaissent aucun fournisseur par son nom : ils ne dépendent que du contrat
décrit ici. Changer de moteur — Kokoro aujourd'hui, Fish Audio demain — revient
à écrire un module qui satisfait ce protocole et à l'enregistrer dans la chaîne
de résolution (`audio.tts_native.get_native_tts_engine`).

**Kokoro est un fournisseur transitoire.** Les optimisations qui lui sont
propres (sidecar maintenu chaud, découpage du premier fragment, protocole de
trames) vivent exclusivement dans les modules listés par
`PROVIDER_SPECIFIC_MODULES`. Leur retrait ne doit toucher ni `vad_utterance`,
ni `stt_daemon`, ni `audio_output`, ni `voice_queue`, ni `voice_latency`, ni le
pipeline `api/voice_*` — c'est vérifié par `tests/test_tts_provider_seam.py`.

Ce qu'un fournisseur doit fournir pour tenir la cible de latence :

- ``synthesize_stream_pcm`` — PCM16 mono **fragment par fragment**. C'est ce
  qui permet de commencer la lecture sur la première phrase au lieu d'attendre
  la synthèse complète. Un fournisseur qui ne sait pas diffuser peut l'omettre :
  le daemon retombe alors sur ``synthesize`` (audio complet), au prix du délai.
- ``warmup`` — charge le modèle **hors tour de parole**. Sans lui, le premier
  énoncé paie le chargement entier.
- ``sample_rate`` — déclaré par le moteur, jamais déduit de son nom.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Protocol, runtime_checkable

# Fréquence retenue quand un moteur ne déclare rien : la sortie PCM doit bien
# ouvrir un flux, et se tromper de fréquence produit une voix déformée.
FALLBACK_SAMPLE_RATE = 24000

# Fichiers qui nomment un fournisseur **par nécessité** : ils forment la chaîne
# de résolution ou exposent l'état de l'installation. Retirer un moteur suppose
# d'y retirer sa branche — liste courte et connue d'avance, pas une découverte
# à faire au moment de la migration.
PROVIDER_AWARE_MODULES: tuple[str, ...] = (
    "audio/tts.py",                          # singletons et chaîne locale
    "audio/tts_native.py",                   # résolution du moteur natif
    "audio/engine_config.py",                # configuration audio résolue
    "config.py",                             # réglages exposés
    "api/misc_integrations.py",              # allowlist du sélecteur de moteur
    "api/misc_status.py",                    # état de l'installation
)

# Modules porteurs d'optimisations propres à un fournisseur. Retirer un moteur
# = supprimer ses modules ici, plus sa branche dans les modules ci-dessus.
PROVIDER_SPECIFIC_MODULES: dict[str, tuple[str, ...]] = {
    "kokoro": (
        "native_audio/kokoro_mlx.py",     # sidecar : mode serveur + découpage
        "native_audio/kokoro_bridge.py",  # client du sidecar chaud
    ),
    "ttskit": (
        "native_audio/ttskit_mlx.py",
        "native_audio/ttskit_bridge.py",
    ),
}

# Modules génériques : aucun nom de fournisseur ne doit y apparaître.
# Ce module-ci en est exclu — il *est* l'inventaire, donc il les nomme.
PROVIDER_AGNOSTIC_MODULES: tuple[str, ...] = (
    "audio/vad_utterance.py",
    "audio/audio_format.py",
    "audio/tts_cache.py",
    "audio/stt_daemon.py",
    "audio/audio_output.py",
    "audio/voice_queue.py",
    "audio/voice_latency.py",
    "api/voice_processing.py",
    "api/voice_fastpath.py",
    "api/voice_prompts.py",
    "scripts/audio_daemon.py",
)


@runtime_checkable
class StreamingTTSProvider(Protocol):
    """Contrat minimal attendu par le daemon audio."""

    def get_backend_name(self) -> str:
        """Nom court du moteur, à des fins de journalisation uniquement."""

    async def synthesize(self, text: str, emotion: str = "neutral") -> bytes:
        """Audio complet (conteneur lisible par la sortie locale)."""

    async def synthesize_stream_pcm(
        self, text: str, emotion: str = "neutral",
    ) -> AsyncGenerator[bytes, None]:
        """PCM16 mono, fragment par fragment, dans l'ordre de lecture."""

    async def warmup(self) -> bool:
        """Charge le moteur hors tour de parole. ``False`` si indisponible."""

    async def shutdown(self) -> None:
        """Libère les ressources tenues entre deux énoncés."""


def provider_sample_rate(engine: Any) -> int:
    """Fréquence déclarée par le moteur, sans jamais déduire de son nom.

    Un branchement sur le nom du fournisseur dans le code générique est
    exactement ce qui rend une migration coûteuse : chaque moteur porte sa
    propre fréquence.
    """
    if engine is None:
        return FALLBACK_SAMPLE_RATE
    for attribute in ("SAMPLE_RATE", "sample_rate"):
        value = getattr(engine, attribute, None)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return FALLBACK_SAMPLE_RATE


def supports_pcm_streaming(engine: Any) -> bool:
    """Le moteur sait-il diffuser du PCM au fil de l'eau ?"""
    return callable(getattr(engine, "synthesize_stream_pcm", None))


__all__ = [
    "FALLBACK_SAMPLE_RATE",
    "PROVIDER_AGNOSTIC_MODULES",
    "PROVIDER_AWARE_MODULES",
    "PROVIDER_SPECIFIC_MODULES",
    "StreamingTTSProvider",
    "provider_sample_rate",
    "supports_pcm_streaming",
]

"""Résolution du fournisseur TTS local — le seul point d'entrée du pipeline.

Aucun module métier n'importe un backend concret : il appelle
``create_local_tts_provider()`` et reçoit quelque chose qui satisfait
``LocalTTSProvider``. C'est ce qui rend le remplacement du moteur mécanique.

La table des fournisseurs est **fermée**. Un nom inconnu lève : il ne doit pas
être possible de faire apparaître un backend — a fortiori distant — par une
simple variable d'environnement.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from jarvis.audio.tts import events
from jarvis.audio.tts.base import LocalTTSProvider
from jarvis.audio.tts.config import KNOWN_PROVIDERS, TTSSettings, load_tts_settings
from jarvis.audio.tts.errors import TTSUnavailableError

logger = logging.getLogger(__name__)

_provider: LocalTTSProvider | None = None


def _fish_local(settings: TTSSettings) -> LocalTTSProvider:
    from jarvis.audio.tts.backends.fish_local import FishLocalTTSProvider

    return FishLocalTTSProvider(settings)


def _current_local(settings: TTSSettings) -> LocalTTSProvider:
    from jarvis.audio.tts.backends.current_local import CurrentLocalTTSProvider

    return CurrentLocalTTSProvider(settings)


# Import différé : construire un fournisseur ne doit charger ni MLX, ni les
# poids, ni le sidecar de l'autre backend.
_BUILDERS: dict[str, Callable[[TTSSettings], LocalTTSProvider]] = {
    "fish_local": _fish_local,
    "current_local": _current_local,
}


def create_local_tts_provider(settings: TTSSettings | None = None) -> LocalTTSProvider:
    """Construit le fournisseur configuré. Ne charge aucun modèle.

    Le chargement appartient à ``warmup()`` : construire un fournisseur doit
    rester instantané, y compris au démarrage de l'application.
    """
    resolved = settings or load_tts_settings()
    builder = _BUILDERS.get(resolved.provider)
    if builder is None:
        raise TTSUnavailableError(
            f"TTS_PROVIDER={resolved.provider!r} inconnu. "
            f"Fournisseurs locaux disponibles : {', '.join(sorted(KNOWN_PROVIDERS))}."
        )

    provider = builder(resolved)
    events.emit_tts_event(events.PROVIDER_CREATED, **provider.info().as_log_fields())
    return provider


def get_local_tts_provider(settings: TTSSettings | None = None) -> LocalTTSProvider:
    """Fournisseur partagé du processus — un seul modèle chargé en mémoire.

    Deux fournisseurs vivants signifieraient deux copies des poids et deux
    générations concurrentes se disputant le GPU.
    """
    global _provider

    if _provider is None:
        _provider = create_local_tts_provider(settings)
    return _provider


async def reset_local_tts_provider() -> None:
    """Ferme le fournisseur partagé (rechargement de configuration, tests)."""
    global _provider

    provider, _provider = _provider, None
    if provider is not None:
        try:
            await provider.close()
        except Exception as exc:  # noqa: BLE001 - la fermeture ne doit rien casser
            logger.warning("[tts] fermeture du fournisseur : %s", exc)


__all__ = [
    "create_local_tts_provider",
    "get_local_tts_provider",
    "reset_local_tts_provider",
]

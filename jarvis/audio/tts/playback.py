"""Lecture des fragments audio locaux — pont vers la sortie CoreAudio.

Ce module ne synthétise rien et ne connaît aucun moteur : il consomme des
``AudioChunk`` et les pousse dans la sortie native déjà en place
(``audio.audio_output``), qui tient la file bornée, le thread de sortie et
l'arrêt immédiat.

La séparation compte : le fournisseur produit du son, la sortie le joue. Un
backend qui écrirait lui-même sur le périphérique rendrait l'interruption et la
mesure du « premier son réellement audible » impossibles à tenir ailleurs.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable

from jarvis.audio.tts.base import AudioChunk

logger = logging.getLogger(__name__)


class PlaybackResult:
    """Compte-rendu d'une lecture — jamais de contenu, uniquement des mesures."""

    __slots__ = ("chunks", "bytes_played", "started")

    def __init__(self) -> None:
        self.chunks = 0
        self.bytes_played = 0
        self.started = False


async def play_chunks(
    chunks: AsyncIterator[AudioChunk],
    *,
    sample_rate: int,
    on_first_chunk: Callable[[], None] | None = None,
    on_playback_started: Callable[[], None] | None = None,
) -> PlaybackResult:
    """Joue un flux de fragments et retourne ce qui a réellement été joué.

    ``sample_rate`` est **déclaré par le fournisseur** (``info().sample_rate``)
    et non déduit du premier fragment : la sortie ouvre son flux avant qu'un
    seul échantillon n'arrive, et se tromper de fréquence déforme la voix sans
    jamais lever d'exception.

    ``on_first_chunk`` se déclenche quand le **premier fragment est produit**
    (le moteur a répondu) ; ``on_playback_started`` quand le périphérique
    **écrit réellement** le premier échantillon. Les confondre fait croire à
    une latence plus courte que celle entendue par l'utilisateur.
    """
    from audio.audio_output import native_audio_output

    result = PlaybackResult()

    async def _pcm_stream():
        async for chunk in chunks:
            if not chunk.data:
                continue
            if chunk.sample_rate != sample_rate:
                # Changer de fréquence en cours de flux déformerait la voix
                # sans jamais lever : on refuse le fragment et on le dit.
                logger.error(
                    "[tts] fragment à %d Hz dans un flux à %d Hz — ignoré",
                    chunk.sample_rate,
                    sample_rate,
                )
                continue
            if result.chunks == 0 and on_first_chunk is not None:
                on_first_chunk()
            result.chunks += 1
            result.bytes_played += len(chunk.data)
            yield chunk.data

    def _started() -> None:
        result.started = True
        if on_playback_started is not None:
            on_playback_started()

    if not native_audio_output.available:
        logger.error("[tts] sortie audio native indisponible — flux non joué")
        # On consomme quand même le flux : le fournisseur doit pouvoir
        # terminer proprement plutôt que rester bloqué sur un consommateur mort.
        async for _ in _pcm_stream():
            pass
        return result

    await native_audio_output.play_stream_from_async(
        _pcm_stream(),
        sample_rate=sample_rate,
        on_first_chunk=_started,
    )
    return result


def stop_playback() -> None:
    """Arrête immédiatement la sortie en cours (barge-in)."""
    from audio.audio_output import native_audio_output

    native_audio_output.stop()


__all__ = ["PlaybackResult", "play_chunks", "stop_playback"]

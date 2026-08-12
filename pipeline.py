"""Contrat indépendant entre les producteurs et le pipeline JARVIS.

`main.py` enregistre les implémentations actuelles après leur définition. Les
daemons dépendent uniquement de ce contrat, ce qui supprime leur import inverse
vers le point d'entrée FastAPI. Les implémentations pourront ensuite être
déplacées progressivement derrière cette interface.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

MessageProcessor = Callable[[str, int, bool], Awaitable[dict[str, Any]]]
VoiceProcessor = Callable[..., Awaitable[dict[str, Any]]]
ContextBuilder = Callable[[str, int], Awaitable[dict[str, Any]]]
CanonicalTurnCallback = Callable[[], Awaitable[None]]


class PipelineNotConfiguredError(RuntimeError):
    """Le point d'entrée a été utilisé avant l'enregistrement du pipeline."""


@dataclass(frozen=True)
class PipelineHandlers:
    process_message: MessageProcessor
    process_voice: VoiceProcessor
    build_context: ContextBuilder


_handlers: PipelineHandlers | None = None


def configure_pipeline(
    *,
    process_message: MessageProcessor,
    process_voice: VoiceProcessor,
    build_context: ContextBuilder,
) -> None:
    """Enregistre atomiquement les implémentations du pipeline au démarrage."""
    global _handlers
    _handlers = PipelineHandlers(
        process_message=process_message,
        process_voice=process_voice,
        build_context=build_context,
    )


def _configured_handlers() -> PipelineHandlers:
    if _handlers is None:
        raise PipelineNotConfiguredError(
            "Pipeline JARVIS non configuré : importer le point d'entrée avant de lancer les daemons."
        )
    return _handlers


async def process_message_internal(
    text: str,
    conversation_id: int,
    voice_mode: bool = False,
) -> dict[str, Any]:
    """Traite un message sans dépendre du module FastAPI."""
    return await _configured_handlers().process_message(
        text, conversation_id, voice_mode
    )


async def process_voice_fast(
    text: str,
    conversation_id: int,
    *,
    stt_ms: int = 0,
    trace: Any | None = None,
    on_canonical_turn_started: CanonicalTurnCallback | None = None,
) -> dict[str, Any]:
    """Traite une phrase vocale via l'implémentation enregistrée.

    ``trace`` transporte la chronologie de latence du tour de parole ; elle est
    facultative pour que les producteurs qui n'en tiennent pas (tests, API)
    restent inchangés.

    ``on_canonical_turn_started`` signale un **changement d'état** au moment où
    le moteur canonique prend le tour. Ce n'est pas un producteur de parole :
    il ne doit rien synthétiser ni jouer. Le rappel part en parallèle du tour
    et n'entre jamais dans son chemin critique.
    """
    kwargs: dict[str, Any] = {"stt_ms": stt_ms, "trace": trace}
    if on_canonical_turn_started is not None:
        kwargs["on_canonical_turn_started"] = on_canonical_turn_started
    return await _configured_handlers().process_voice(text, conversation_id, **kwargs)


async def build_enriched_context(
    text: str, conversation_id: int
) -> dict[str, Any]:
    """Construit le contexte via l'implémentation enregistrée."""
    return await _configured_handlers().build_context(text, conversation_id)

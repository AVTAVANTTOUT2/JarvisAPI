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
VoiceProcessor = Callable[[str, int], Awaitable[dict[str, Any]]]
ContextBuilder = Callable[[str, int], Awaitable[dict[str, Any]]]


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


async def process_voice_fast(text: str, conversation_id: int) -> dict[str, Any]:
    """Traite une phrase vocale via l'implémentation enregistrée."""
    return await _configured_handlers().process_voice(text, conversation_id)


async def build_enriched_context(
    text: str, conversation_id: int
) -> dict[str, Any]:
    """Construit le contexte via l'implémentation enregistrée."""
    return await _configured_handlers().build_context(text, conversation_id)

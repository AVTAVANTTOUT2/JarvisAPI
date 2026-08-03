"""Instrumentation de la synthèse vocale locale.

La règle est la même que dans ``audio.voice_latency`` et n'est pas négociable :
**aucun contenu ne franchit cette frontière**. On journalise des longueurs, des
noms de moteur, des durées et des identifiants de corrélation — jamais un
texte, une transcription ou un chemin absolu.

Les noms d'événements sont fermés : une faute de frappe lève plutôt que de
créer une étape orpheline, invisible dans les agrégations.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("tts_events")

PROVIDER_CREATED = "tts.provider.created"
WARMUP_STARTED = "tts.warmup.started"
WARMUP_COMPLETED = "tts.warmup.completed"
QUEUE_ENTERED = "tts.queue.entered"
SEGMENT_RECEIVED = "tts.segment.received"
SYNTHESIS_STARTED = "tts.synthesis.started"
FIRST_CHUNK = "tts.first_chunk"
PLAYBACK_STARTED = "tts.playback.started"
SYNTHESIS_COMPLETED = "tts.synthesis.completed"
PLAYBACK_COMPLETED = "tts.playback.completed"
CANCELLED = "tts.cancelled"
FAILED = "tts.failed"

KNOWN_EVENTS: frozenset[str] = frozenset({
    PROVIDER_CREATED,
    WARMUP_STARTED,
    WARMUP_COMPLETED,
    QUEUE_ENTERED,
    SEGMENT_RECEIVED,
    SYNTHESIS_STARTED,
    FIRST_CHUNK,
    PLAYBACK_STARTED,
    SYNTHESIS_COMPLETED,
    PLAYBACK_COMPLETED,
    CANCELLED,
    FAILED,
})

# Champs autorisés. Tout le reste est **jeté**, pas journalisé : c'est la
# garantie mécanique qu'un texte ne passe pas par inadvertance dans un log.
ALLOWED_FIELDS: frozenset[str] = frozenset({
    "provider",
    "backend",
    "device",
    "model",
    "voice",
    "streaming",
    "chars",            # longueur du texte, jamais le texte
    "segment_index",
    "queue_ms",
    "warmup_ms",
    "first_chunk_ms",
    "playback_start_ms",
    "synthesis_ms",
    "playback_ms",
    "total_ms",
    "bytes",
    "sample_rate",
    "channels",
    "offline",
    "request_id",
    "utterance_id",
    "conversation_id",
    "reason",           # étiquette fermée (ex. "barge_in", "model_missing")
})


def sanitize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Ne conserve que les champs autorisés et non nuls."""
    return {
        key: value
        for key, value in fields.items()
        if key in ALLOWED_FIELDS and value is not None
    }


def emit_tts_event(event: str, **fields: Any) -> dict[str, Any]:
    """Journalise un événement TTS et retourne les champs retenus.

    Le retour sert aux tests et aux agrégations : il permet de vérifier ce qui
    a réellement été publié, sans relire des lignes de log.
    """
    if event not in KNOWN_EVENTS:
        raise ValueError(f"événement TTS inconnu : {event!r}")
    clean = sanitize_fields(fields)
    logger.info(
        "%s %s",
        event,
        " ".join(f"{key}={value}" for key, value in sorted(clean.items())),
    )
    return clean


__all__ = [
    "ALLOWED_FIELDS",
    "CANCELLED",
    "FAILED",
    "FIRST_CHUNK",
    "KNOWN_EVENTS",
    "PLAYBACK_COMPLETED",
    "PLAYBACK_STARTED",
    "PROVIDER_CREATED",
    "QUEUE_ENTERED",
    "SEGMENT_RECEIVED",
    "SYNTHESIS_COMPLETED",
    "SYNTHESIS_STARTED",
    "WARMUP_COMPLETED",
    "WARMUP_STARTED",
    "emit_tts_event",
    "sanitize_fields",
]

"""Instrumentation de latence du pipeline vocal — horloge monotone, corrélée par énoncé.

Un tour de parole traverse quatre sous-systèmes (VAD, STT, LLM, TTS) répartis
sur plusieurs tâches asyncio et deux threads. Sans identifiant commun, chaque
sous-système ne peut mesurer que lui-même et les cinq secondes « entre deux »
restent invisibles. ``UtteranceTrace`` porte cet identifiant et n'utilise que
``time.perf_counter()`` : un ajustement d'horloge système ne doit pas produire
une durée négative dans un rapport de performance.

Règle de confidentialité : aucune transcription, aucun texte de réponse, aucun
jeton ne franchit cette frontière. On journalise des **longueurs**, des noms de
moteur et des durées — jamais le contenu.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("voice_latency")

# ── Noms d'étapes canoniques ────────────────────────────────────────────────
# Toute étape journalisée doit venir d'ici : un test verrouille la liste, de
# sorte qu'une faute de frappe ne crée pas une étape orpheline invisible dans
# les agrégations.

SEGMENT_STARTED = "voice.segment.started"
SEGMENT_SPEECH_STARTED = "voice.segment.speech_started"
SEGMENT_SPEECH_ENDED = "voice.segment.speech_ended"
SEGMENT_FINALIZED = "voice.segment.finalized"

STT_QUEUE_ENTERED = "stt.queue.entered"
STT_STARTED = "stt.started"
STT_COMPLETED = "stt.completed"

CONVERSATION_LOOKUP_STARTED = "conversation.lookup.started"
CONVERSATION_LOOKUP_COMPLETED = "conversation.lookup.completed"
USER_MESSAGE_PERSIST_STARTED = "user_message.persist.started"
USER_MESSAGE_PERSIST_COMPLETED = "user_message.persist.completed"
CONTEXT_BUILD_STARTED = "context.build.started"
CONTEXT_BUILD_COMPLETED = "context.build.completed"

LLM_QUEUE_ENTERED = "llm.queue.entered"
LLM_REQUEST_STARTED = "llm.request.started"
LLM_FIRST_TOKEN = "llm.first_token"
LLM_COMPLETED = "llm.completed"

ASSISTANT_MESSAGE_PERSIST_STARTED = "assistant_message.persist.started"
ASSISTANT_MESSAGE_PERSIST_COMPLETED = "assistant_message.persist.completed"

TTS_QUEUE_ENTERED = "tts.queue.entered"
TTS_MODEL_READY = "tts.model.ready"
TTS_SYNTHESIS_STARTED = "tts.synthesis.started"
TTS_FIRST_AUDIO_CHUNK = "tts.first_audio_chunk"
TTS_PLAYBACK_STARTED = "tts.playback.started"
TTS_SYNTHESIS_COMPLETED = "tts.synthesis.completed"
TTS_PLAYBACK_COMPLETED = "tts.playback.completed"

PIPELINE_REARMED = "voice.pipeline.rearmed"

KNOWN_EVENTS: frozenset[str] = frozenset({
    SEGMENT_STARTED,
    SEGMENT_SPEECH_STARTED,
    SEGMENT_SPEECH_ENDED,
    SEGMENT_FINALIZED,
    STT_QUEUE_ENTERED,
    STT_STARTED,
    STT_COMPLETED,
    CONVERSATION_LOOKUP_STARTED,
    CONVERSATION_LOOKUP_COMPLETED,
    USER_MESSAGE_PERSIST_STARTED,
    USER_MESSAGE_PERSIST_COMPLETED,
    CONTEXT_BUILD_STARTED,
    CONTEXT_BUILD_COMPLETED,
    LLM_QUEUE_ENTERED,
    LLM_REQUEST_STARTED,
    LLM_FIRST_TOKEN,
    LLM_COMPLETED,
    ASSISTANT_MESSAGE_PERSIST_STARTED,
    ASSISTANT_MESSAGE_PERSIST_COMPLETED,
    TTS_QUEUE_ENTERED,
    TTS_MODEL_READY,
    TTS_SYNTHESIS_STARTED,
    TTS_FIRST_AUDIO_CHUNK,
    TTS_PLAYBACK_STARTED,
    TTS_SYNTHESIS_COMPLETED,
    TTS_PLAYBACK_COMPLETED,
    PIPELINE_REARMED,
})

# Champs autorisés dans un mark. Tout le reste est refusé plutôt que
# journalisé : c'est la garantie qu'aucun texte ne passe par accident.
ALLOWED_FIELDS: frozenset[str] = frozenset({
    "engine",           # nom du moteur (faster-whisper, kokoro, deepseek…)
    "audio_ms",         # durée de l'audio capturé
    "text_chars",       # longueur du texte, jamais le texte
    "queue_depth",      # profondeur de file au moment du mark
    "task",             # nom de la tâche asyncio / du thread
    "beam_size",
    "compute_type",
    "device",
    "model",
    "real_time_factor",
    "chunk_index",      # index du fragment TTS
    "sample_rate",
    "reason",           # étiquette fermée (ex. "empty_transcript")
    "cold",             # premier appel du moteur (bool)
    "ok",
})


def _current_task_name() -> str:
    """Nom de la tâche asyncio courante, ou du thread si hors boucle."""
    import asyncio
    import threading

    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    if task is not None:
        return f"task:{task.get_name()}"
    return f"thread:{threading.current_thread().name}"


@dataclass(frozen=True)
class Mark:
    event: str
    at: float           # perf_counter absolu
    since_start_ms: float
    since_previous_ms: float
    fields: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "since_start_ms": round(self.since_start_ms, 1),
            "since_previous_ms": round(self.since_previous_ms, 1),
            **self.fields,
        }


@dataclass
class UtteranceTrace:
    """Chronologie d'un tour de parole, de la capture au réarmement.

    L'origine (``t0``) est la **fin de parole** détectée par le VAD, pas le
    début de la capture : c'est l'instant à partir duquel l'utilisateur attend
    une réponse, donc le seul point de référence honnête pour
    ``end_of_speech_to_first_audio_ms``.
    """

    utterance_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    conversation_id: int | None = None
    t0: float = field(default_factory=time.perf_counter)
    marks: list[Mark] = field(default_factory=list)
    _by_event: dict[str, Mark] = field(default_factory=dict, repr=False)

    # ── Écriture ────────────────────────────────────────────────────────────

    def mark(self, event: str, **fields: Any) -> Mark:
        """Enregistre une étape. Les champs hors allowlist sont ignorés."""
        now = time.perf_counter()
        previous = self.marks[-1].at if self.marks else self.t0
        clean = {k: v for k, v in fields.items() if k in ALLOWED_FIELDS and v is not None}
        clean.setdefault("task", _current_task_name())
        entry = Mark(
            event=event,
            at=now,
            since_start_ms=(now - self.t0) * 1000.0,
            since_previous_ms=(now - previous) * 1000.0,
            fields=clean,
        )
        self.marks.append(entry)
        # Première occurrence conservée : un moteur qui retente ne doit pas
        # écraser l'instant réel du premier passage.
        self._by_event.setdefault(event, entry)

        if event not in KNOWN_EVENTS:
            logger.warning(
                "[voice_latency] étape inconnue %r (utterance=%s)", event, self.utterance_id,
            )

        logger.debug(
            "[voice_latency] %s utterance=%s conv=%s +%.0fms total=%.0fms %s",
            event,
            self.utterance_id,
            self.conversation_id,
            entry.since_previous_ms,
            entry.since_start_ms,
            clean,
        )
        return entry

    def set_conversation(self, conversation_id: int | None) -> None:
        self.conversation_id = conversation_id

    # ── Lecture ─────────────────────────────────────────────────────────────

    def elapsed_ms(self, event: str) -> float | None:
        """Millisecondes entre la fin de parole et ``event``, ou None."""
        entry = self._by_event.get(event)
        return None if entry is None else round(entry.since_start_ms, 1)

    def span_ms(self, start_event: str, end_event: str) -> float | None:
        """Durée entre deux étapes, ou None si l'une des deux manque."""
        a = self._by_event.get(start_event)
        b = self._by_event.get(end_event)
        if a is None or b is None:
            return None
        return round((b.at - a.at) * 1000.0, 1)

    @property
    def end_of_speech_to_first_audio_ms(self) -> float | None:
        """Métrique principale : fin de parole → premier son réellement joué."""
        return self.elapsed_ms(TTS_PLAYBACK_STARTED)

    def snapshot(self) -> dict[str, Any]:
        """Vue sérialisable — sans aucun contenu textuel."""
        return {
            "utterance_id": self.utterance_id,
            "conversation_id": self.conversation_id,
            "end_of_speech_to_first_audio_ms": self.end_of_speech_to_first_audio_ms,
            "stt_ms": self.span_ms(STT_STARTED, STT_COMPLETED),
            "stt_queue_ms": self.span_ms(STT_QUEUE_ENTERED, STT_STARTED),
            "llm_first_token_ms": self.elapsed_ms(LLM_FIRST_TOKEN),
            "llm_ms": self.span_ms(LLM_REQUEST_STARTED, LLM_COMPLETED),
            "tts_first_audio_ms": self.elapsed_ms(TTS_FIRST_AUDIO_CHUNK),
            "tts_synthesis_ms": self.span_ms(TTS_SYNTHESIS_STARTED, TTS_SYNTHESIS_COMPLETED),
            "playback_ms": self.span_ms(TTS_PLAYBACK_STARTED, TTS_PLAYBACK_COMPLETED),
            "rearmed_ms": self.elapsed_ms(PIPELINE_REARMED),
            "total_ms": round(
                (self.marks[-1].at - self.t0) * 1000.0, 1,
            ) if self.marks else 0.0,
            "steps": [m.to_dict() for m in self.marks],
        }

    def log_summary(self, *, reason: str = "completed") -> dict[str, Any]:
        """Journalise la ligne de synthèse et retourne le snapshot."""
        snap = self.snapshot()
        logger.info(
            "[voice_latency] utterance=%s reason=%s first_audio=%sms stt=%sms "
            "llm_first_token=%sms tts_first_audio=%sms total=%sms",
            self.utterance_id,
            reason,
            snap["end_of_speech_to_first_audio_ms"],
            snap["stt_ms"],
            snap["llm_first_token_ms"],
            snap["tts_first_audio_ms"],
            snap["total_ms"],
        )
        return snap


class NullTrace(UtteranceTrace):
    """Trace inerte — pour les chemins appelés hors tour de parole (tests, notifs)."""

    def mark(self, event: str, **fields: Any) -> Mark:  # noqa: D102
        now = time.perf_counter()
        return Mark(event=event, at=now, since_start_ms=0.0, since_previous_ms=0.0, fields={})

    def log_summary(self, *, reason: str = "completed") -> dict[str, Any]:  # noqa: D102
        return {}


def new_trace(conversation_id: int | None = None) -> UtteranceTrace:
    return UtteranceTrace(conversation_id=conversation_id)


__all__ = [
    "ALLOWED_FIELDS",
    "KNOWN_EVENTS",
    "Mark",
    "NullTrace",
    "UtteranceTrace",
    "new_trace",
    # étapes
    "ASSISTANT_MESSAGE_PERSIST_COMPLETED",
    "ASSISTANT_MESSAGE_PERSIST_STARTED",
    "CONTEXT_BUILD_COMPLETED",
    "CONTEXT_BUILD_STARTED",
    "CONVERSATION_LOOKUP_COMPLETED",
    "CONVERSATION_LOOKUP_STARTED",
    "LLM_COMPLETED",
    "LLM_FIRST_TOKEN",
    "LLM_QUEUE_ENTERED",
    "LLM_REQUEST_STARTED",
    "PIPELINE_REARMED",
    "SEGMENT_FINALIZED",
    "SEGMENT_SPEECH_ENDED",
    "SEGMENT_SPEECH_STARTED",
    "SEGMENT_STARTED",
    "STT_COMPLETED",
    "STT_QUEUE_ENTERED",
    "STT_STARTED",
    "TTS_FIRST_AUDIO_CHUNK",
    "TTS_MODEL_READY",
    "TTS_PLAYBACK_COMPLETED",
    "TTS_PLAYBACK_STARTED",
    "TTS_QUEUE_ENTERED",
    "TTS_SYNTHESIS_COMPLETED",
    "TTS_SYNTHESIS_STARTED",
    "USER_MESSAGE_PERSIST_COMPLETED",
    "USER_MESSAGE_PERSIST_STARTED",
]

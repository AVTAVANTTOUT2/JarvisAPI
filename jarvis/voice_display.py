"""État temps réel, borné et non bloquant du JARVIS Voice HUD."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import unicodedata
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

import config

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DisplayState = Literal[
    "idle", "listening", "understanding", "researching", "result", "speaking", "error"
]
SourceStatus = Literal[
    "discovered", "fetching", "verified", "used", "rejected", "unavailable", "conflicting"
]
Certainty = Literal["confirmed", "probable", "estimate", "unverified", "conflicting"]

_EVENT_TYPES = frozenset({
    "voice.session.started", "voice.session.completed", "voice.wake.detected",
    "voice.listening.started", "voice.listening.completed", "voice.transcript.partial",
    "voice.transcript.final", "voice.request.understood", "voice.tool.started",
    "voice.tool.completed", "voice.tool.failed", "voice.source.discovered",
    "voice.source.verified", "voice.source.used", "voice.source.rejected",
    "voice.source.failed", "voice.result.started", "voice.result.final",
    "voice.result.failed", "voice.speech.started", "voice.speech.segment.started",
    "voice.speech.segment.completed", "voice.speech.paused", "voice.speech.resumed",
    "voice.speech.interrupted", "voice.speech.completed",
    "voice.display.focus.changed", "voice.display.view.opened", "voice.display.view.closed",
    "voice.display.back", "voice.display.cleared", "voice.display.privacy.enabled",
    "voice.display.privacy.disabled", "voice.microphone.muted", "voice.microphone.unmuted",
    "voice.error",
})
_SECRET_RE = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._-]{12,}|(?:api[_-]?key|token|secret)\s*[:=]\s*\S+|sk-[a-z0-9_-]{8,})"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any, limit: int = 2_000) -> str:
    return _SECRET_RE.sub("[masqué]", str(value or "").strip())[:limit]


def _safe_data(value: Any, *, depth: int = 0, key: str = "") -> Any:
    if depth > 3:
        return None
    if isinstance(value, Mapping):
        return {
            _text(raw_key, 80): _safe_data(item, depth=depth + 1, key=str(raw_key))
            for raw_key, item in list(value.items())[:24]
            if not re.search(r"(?i)(token|secret|password|api.?key)", str(raw_key))
        }
    if isinstance(value, (list, tuple)):
        return [_safe_data(item, depth=depth + 1, key=key) for item in list(value)[:12]]
    if isinstance(value, str):
        limit = 500 if key.casefold() in {"body", "content", "html"} else 1_200
        safe = _text(value, limit)
        if key.casefold() in {"path", "locator"} and Path(safe).is_absolute():
            return Path(safe).name
        return safe
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _text(value, 300)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceEvidence(StrictModel):
    id: str
    kind: str = "internal"
    title: str
    provider: str | None = None
    domain: str | None = None
    url: str | None = None
    locator: str | None = None
    fetched_at: datetime | None = None
    published_at: datetime | None = None
    status: SourceStatus = "discovered"
    used: bool = False
    excerpt: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class ClaimEvidence(StrictModel):
    id: str
    text: str
    value: str | int | float | bool | None = None
    certainty: Certainty = "unverified"
    source_ids: list[str] = Field(default_factory=list)
    status: str = "unverified"
    conflict: bool = False
    notes: str | None = None

    @model_validator(mode="after")
    def confirmed_has_source(self) -> "ClaimEvidence":
        if self.certainty == "confirmed" and not self.source_ids:
            raise ValueError("une affirmation confirmée exige une source réelle")
        return self


class VoiceAction(StrictModel):
    id: str
    label: str
    intent: str
    aliases: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    risk_level: Literal["low", "medium", "high"] = "low"
    requires_confirmation: bool = False


class SpeechSegment(StrictModel):
    segment_id: str
    text: str
    visual_target_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    order: int


class VisualSection(StrictModel):
    id: str
    type: Literal[
        "summary", "key_points", "ranked_results", "recommendation", "comparison",
        "source_list", "source_reader", "email", "calendar", "route", "location",
        "code_result", "file_result", "image_gallery", "progress", "warning",
        "error", "confirmation",
    ]
    title: str
    order: int
    data: dict[str, Any] = Field(default_factory=dict)
    focusable_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class VisualAnswer(StrictModel):
    title: str = "Réponse"
    spoken_summary: str
    visual_summary: str
    sections: list[VisualSection] = Field(default_factory=list)
    sources: list[SourceEvidence] = Field(default_factory=list)
    claims: list[ClaimEvidence] = Field(default_factory=list)
    suggested_voice_actions: list[VoiceAction] = Field(default_factory=list)
    speech_segments: list[SpeechSegment] = Field(default_factory=list)
    status: Literal["building", "partial", "complete", "failed"] = "complete"
    created_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = Field(default_factory=_now)


class VoiceDisplaySession(StrictModel):
    session_id: str = "voice-display-idle"
    turn_id: str | None = None
    conversation_id: int | None = None
    state: DisplayState = "idle"
    started_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    locale: str = "fr-FR"
    privacy_mode: bool = False
    microphone_state: Literal["unknown", "listening", "muted", "unavailable"] = "unknown"
    transcript_partial: str = ""
    transcript_final: str = ""
    understood_request: dict[str, Any] = Field(default_factory=dict)
    current_focus: dict[str, Any] | None = None
    navigation_stack: list[dict[str, Any]] = Field(default_factory=list)
    answer: VisualAnswer | None = None
    activities: list[dict[str, Any]] = Field(default_factory=list)
    active_speech_segment_id: str | None = None
    last_sequence: int = 0


class VoiceDisplayEvent(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    sequence: int = Field(ge=1)
    event_id: str
    emitted_at: datetime = Field(default_factory=_now)
    session_id: str
    turn_id: str | None = None
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    privacy: Literal["public", "private"] = "private"

    @model_validator(mode="after")
    def supported_event(self) -> "VoiceDisplayEvent":
        if self.type not in _EVENT_TYPES:
            raise ValueError(f"événement Voice HUD inconnu : {self.type}")
        return self


class VoiceDisplaySnapshot(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    enabled: bool = True
    generated_at: datetime = Field(default_factory=_now)
    privacy_timeout_seconds: int
    session: VoiceDisplaySession


class VoiceDisplaySubscription:
    def __init__(self, maxsize: int = 128) -> None:
        self.queue: asyncio.Queue[VoiceDisplayEvent] = asyncio.Queue(maxsize=maxsize)
        self.closed = False

    async def get(self) -> VoiceDisplayEvent:
        return await self.queue.get()


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    return parts[:12] or ([text.strip()] if text.strip() else [])


def _source_from_mapping(item: Mapping[str, Any], verified: set[str]) -> SourceEvidence | None:
    source_type = _text(item.get("source_type") or item.get("kind") or item.get("provider"), 80)
    source_id = _text(
        item.get("uid") or item.get("source_id") or item.get("canonical_id") or item.get("id"),
        256,
    )
    if not source_id and not source_type:
        return None
    source_id = source_id or f"{source_type}:{uuid.uuid4().hex[:8]}"
    provenance = item.get("provenance") if isinstance(item.get("provenance"), Mapping) else {}
    locator = _text(
        item.get("locator") or provenance.get("locator") or provenance.get("path"), 512
    ) or None
    if locator and Path(locator).is_absolute():
        locator = Path(locator).name
    url = _text(item.get("url") or provenance.get("url"), 1_000) or None
    status: SourceStatus = "verified" if source_type in verified else "discovered"
    return SourceEvidence(
        id=source_id,
        kind=source_type or "internal",
        title=_text(item.get("title") or item.get("reference") or source_type or "Source", 240),
        provider=source_type or None,
        domain=_text(item.get("domain") or provenance.get("domain"), 160) or None,
        url=url if url and url.startswith(("http://", "https://")) else None,
        locator=locator,
        fetched_at=_now(),
        status=status,
        used=status == "verified",
        excerpt=_text(item.get("excerpt") or item.get("summary"), 1_200) or None,
        metadata={"source_type": source_type} if source_type else {},
    )


def answer_from_result(result: Mapping[str, Any] | None, text: str) -> VisualAnswer:
    result = result or {}
    knowledge = result.get("knowledge") if isinstance(result.get("knowledge"), Mapping) else {}
    action_result = (
        result.get("action_result") if isinstance(result.get("action_result"), Mapping) else {}
    )
    verified = {
        _text(value, 80)
        for container in (knowledge, action_result, action_result.get("knowledge", {}))
        if isinstance(container, Mapping)
        for value in list(container.get("verified_sources") or [])
    }
    candidates: list[Mapping[str, Any]] = []
    candidates.extend(x for x in list(action_result.get("data") or []) if isinstance(x, Mapping))
    for container in (knowledge, action_result.get("knowledge", {})):
        if isinstance(container, Mapping):
            candidates.extend(x for x in list(container.get("references") or []) if isinstance(x, Mapping))

    sources: list[SourceEvidence] = []
    seen: set[str] = set()
    for item in candidates:
        source = _source_from_mapping(item, verified)
        if source and source.id not in seen:
            sources.append(source)
            seen.add(source.id)
        if len(sources) >= 12:
            break

    claims = [
        ClaimEvidence(
            id=f"claim-{index}",
            text=source.excerpt,
            certainty="confirmed" if source.status in {"verified", "used"} else "unverified",
            source_ids=[source.id] if source.status in {"verified", "used"} else [],
            status=source.status,
        )
        for index, source in enumerate(sources, 1)
        if source.excerpt
    ]
    sections = [
        VisualSection(id="summary", type="summary", title="Réponse", order=0, data={"text": _text(text, 8_000)})
    ]
    data = _safe_data(list(action_result.get("data") or []))
    if data:
        action_type = _text((result.get("action") or {}).get("type") if isinstance(result.get("action"), Mapping) else "")
        section_type = "email" if action_type == "mail_read" else "ranked_results"
        sections.append(VisualSection(
            id="results", type=section_type, title="Résultats", order=1,
            data={"items": data[:12]}, focusable_ids=[f"result-{i}" for i in range(1, min(12, len(data)) + 1)],
            source_ids=[source.id for source in sources],
        ))
    if sources:
        sections.append(VisualSection(
            id="sources", type="source_list", title="Sources consultées", order=len(sections),
            data={"count": len(sources)}, focusable_ids=[source.id for source in sources],
            source_ids=[source.id for source in sources],
        ))

    segments = [
        SpeechSegment(
            segment_id=f"speech-{index}", text=sentence,
            visual_target_ids=["summary"], source_ids=[source.id for source in sources[:3]], order=index,
        )
        for index, sentence in enumerate(_sentences(text), 1)
    ]
    actions = [
        VoiceAction(id="next", label="Suivant", intent="voice_display.next", aliases=["élément suivant"]),
        VoiceAction(id="open-source", label="Ouvre la source 1", intent="voice_display.open_source", aliases=["montre la première source"]),
        VoiceAction(id="back", label="Reviens", intent="voice_display.back", aliases=["retour aux résultats"]),
        VoiceAction(id="privacy", label="Masque l’écran", intent="voice_display.privacy", aliases=["mode privé"]),
    ]
    return VisualAnswer(
        spoken_summary=_text(text, 8_000), visual_summary=_text(text, 8_000),
        sections=sections, sources=sources, claims=claims,
        suggested_voice_actions=actions[:4], speech_segments=segments,
    )


class VoiceDisplayCoordinator:
    """Reducer backend et fan-out sans attente du frontend."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sequence = 0
        self._events: deque[VoiceDisplayEvent] = deque(maxlen=self._retention())
        self._subscriptions: set[VoiceDisplaySubscription] = set()
        self._session = VoiceDisplaySession(locale=getattr(config, "LANGUAGE", "fr-FR"))

    @staticmethod
    def enabled() -> bool:
        return bool(getattr(config, "VOICE_DISPLAY_ENABLED", False))

    @staticmethod
    def _retention() -> int:
        return max(32, int(getattr(config, "VOICE_DISPLAY_EVENT_RETENTION", 512)))

    def reset(self) -> None:
        with self._lock:
            self._sequence = 0
            self._events = deque(maxlen=self._retention())
            self._session = VoiceDisplaySession(locale=getattr(config, "LANGUAGE", "fr-FR"))

    def safely(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Frontière fail-open : le HUD ne casse jamais le pipeline vocal."""
        if not self.enabled():
            return None
        try:
            return getattr(self, method)(*args, **kwargs)
        except Exception:
            logger.warning("[voice-display] événement ignoré", exc_info=True)
            return None

    def ensure_turn(self, conversation_id: int | None = None) -> None:
        if self._session.state not in {"idle", "result", "error"} and (
            conversation_id is None or self._session.conversation_id == conversation_id
        ):
            return
        privacy_mode = self._session.privacy_mode
        session_id = f"voice-{conversation_id or uuid.uuid4().hex[:12]}"
        turn_id = f"turn-{uuid.uuid4().hex[:12]}"
        self._session = VoiceDisplaySession(
            session_id=session_id, turn_id=turn_id, conversation_id=conversation_id,
            state="listening", microphone_state="listening",
            locale=getattr(config, "LANGUAGE", "fr-FR"),
            privacy_mode=privacy_mode,
        )
        self.publish("voice.session.started", {"conversation_id": conversation_id})

    def publish(self, event_type: str, payload: Mapping[str, Any] | None = None) -> VoiceDisplayEvent | None:
        if not self.enabled():
            return None
        with self._lock:
            self._sequence += 1
            event = VoiceDisplayEvent(
                sequence=self._sequence, event_id=f"evt-{uuid.uuid4().hex}",
                session_id=self._session.session_id, turn_id=self._session.turn_id,
                type=event_type, payload=dict(payload or {}),
            )
            self._apply(event)
            self._events.append(event)
            for subscription in tuple(self._subscriptions):
                if subscription.closed:
                    self._subscriptions.discard(subscription)
                    continue
                if subscription.queue.full():
                    try:
                        subscription.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                subscription.queue.put_nowait(event)
            return event

    def _apply(self, event: VoiceDisplayEvent) -> None:
        session = self._session
        payload = event.payload
        if event.type == "voice.listening.started":
            session.state, session.microphone_state = "listening", "listening"
        elif event.type == "voice.session.completed":
            session.state, session.microphone_state = "idle", "unknown"
        elif event.type == "voice.transcript.partial":
            session.transcript_partial = _text(payload.get("text"), 2_000)
        elif event.type == "voice.transcript.final":
            session.transcript_final = _text(payload.get("text"), 4_000)
            session.transcript_partial = ""
            session.state = "understanding"
        elif event.type == "voice.request.understood":
            session.understood_request = dict(payload)
            session.state = "understanding"
        elif event.type.startswith("voice.tool."):
            session.state = "researching"
            session.activities = (session.activities + [dict(payload)])[-20:]
        elif event.type == "voice.result.final":
            session.answer = VisualAnswer.model_validate(payload["answer"])
            session.state = "result"
        elif event.type in {"voice.speech.started", "voice.speech.resumed"}:
            session.state = "speaking"
        elif event.type == "voice.speech.segment.started":
            session.active_speech_segment_id = _text(payload.get("segment_id"), 120) or None
            session.state = "speaking"
        elif event.type in {"voice.speech.interrupted", "voice.speech.completed"}:
            session.active_speech_segment_id = None
            session.state = "result" if session.answer else "idle"
        elif event.type == "voice.display.focus.changed":
            session.current_focus = dict(payload)
        elif event.type == "voice.display.view.opened":
            if session.current_focus:
                session.navigation_stack = (session.navigation_stack + [session.current_focus])[-20:]
            session.current_focus = dict(payload)
        elif event.type == "voice.display.back":
            session.current_focus = session.navigation_stack.pop() if session.navigation_stack else None
        elif event.type == "voice.display.cleared":
            private = session.privacy_mode
            self._session = VoiceDisplaySession(locale=session.locale, privacy_mode=private)
            session = self._session
        elif event.type == "voice.display.privacy.enabled":
            session.privacy_mode = True
        elif event.type == "voice.display.privacy.disabled":
            session.privacy_mode = False
        elif event.type in {"voice.error", "voice.result.failed"}:
            session.state = "error"
            session.activities = (session.activities + [{"label": _text(payload.get("message"), 500), "status": "failed"}])[-20:]
        session.updated_at = event.emitted_at
        session.last_sequence = event.sequence

    def snapshot(self) -> VoiceDisplaySnapshot:
        with self._lock:
            return VoiceDisplaySnapshot(
                privacy_timeout_seconds=max(30, int(getattr(config, "VOICE_DISPLAY_PRIVACY_TIMEOUT_SECONDS", 300))),
                session=VoiceDisplaySession.model_validate(self._session.model_dump()),
            )

    def replay(self, since: int) -> list[VoiceDisplayEvent]:
        with self._lock:
            return [event for event in self._events if event.sequence > since]

    def subscribe(self) -> VoiceDisplaySubscription:
        subscription = VoiceDisplaySubscription()
        with self._lock:
            self._subscriptions.add(subscription)
        return subscription

    def unsubscribe(self, subscription: VoiceDisplaySubscription) -> None:
        subscription.closed = True
        with self._lock:
            self._subscriptions.discard(subscription)

    def transcript(self, text: str, *, partial: bool = False, conversation_id: int | None = None) -> None:
        self.ensure_turn(conversation_id)
        self.publish("voice.transcript.partial" if partial else "voice.transcript.final", {"text": _text(text, 4_000)})

    def processing(self, label: str = "Analyse de la demande") -> None:
        self.publish("voice.tool.started", {"id": "canonical-turn", "label": _text(label, 160), "status": "running"})

    def result(self, result: Mapping[str, Any] | None, text: str) -> VisualAnswer:
        answer = answer_from_result(result, text)
        for source in answer.sources:
            self.publish(
                "voice.source.verified" if source.status == "verified" else "voice.source.discovered",
                {"source": source.model_dump(mode="json")},
            )
        self.publish("voice.tool.completed", {"id": "canonical-turn", "label": "Analyse terminée", "status": "completed"})
        self.publish("voice.result.final", {"answer": answer.model_dump(mode="json")})
        return answer

    def speech_started(self) -> None:
        self.publish("voice.speech.started")
        answer = self._session.answer
        if answer and answer.speech_segments:
            self.publish("voice.speech.segment.started", answer.speech_segments[0].model_dump(mode="json"))

    def speech_finished(self, *, interrupted: bool = False) -> None:
        self.publish("voice.speech.interrupted" if interrupted else "voice.speech.completed")

    def ingest_audio_daemon_event(self, raw: Mapping[str, Any]) -> None:
        if not self.enabled() or raw.get("type") in {"voice_debug_stt", "voice_debug_tts"}:
            return
        state = _text(raw.get("state"), 40)
        if raw.get("transcript"):
            self.transcript(_text(raw["transcript"], 4_000))
            self.processing()
        if raw.get("response") is not None:
            result = {
                "action": raw.get("action"), "action_result": raw.get("action_result"),
                "knowledge": raw.get("knowledge"),
            }
            self.result(result, _text(raw.get("response"), 8_000))
        if state in {"listening", "wake_listening"}:
            if self._session.state == "speaking":
                self.speech_finished()
            self.publish("voice.listening.started")
        elif state == "processing":
            self.processing()
        elif state == "speaking" and self._session.state != "speaking":
            self.speech_started()
        elif state in {"sleep", "sleeping", "idle"}:
            self.publish("voice.session.completed")

    def navigation_command(self, text: str) -> dict[str, Any] | None:
        if not self.enabled():
            return None
        normalized = "".join(
            char for char in unicodedata.normalize("NFD", text.casefold())
            if unicodedata.category(char) != "Mn"
        )
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
        if normalized in {"masque l ecran", "active le mode prive", "mode prive"}:
            self.publish("voice.display.privacy.enabled")
            return {"reply": "Contenu masqué.", "intent": "privacy.enabled"}
        if normalized in {"desactive le mode prive", "affiche l ecran"}:
            self.publish("voice.display.privacy.disabled")
            return {"reply": "Affichage restauré.", "intent": "privacy.disabled"}
        if normalized in {"efface l ecran", "efface la conversation"}:
            self.publish("voice.display.cleared")
            return {"reply": "Écran effacé.", "intent": "display.cleared"}
        if normalized in {"repasse en veille", "retourne en veille"}:
            self.publish("voice.display.cleared")
            return {"reply": "Je reste en veille.", "intent": "display.idle"}
        if normalized in {"reviens", "retour", "retour aux resultats", "reviens aux resultats"}:
            self.publish("voice.display.back")
            return {"reply": "Retour aux résultats.", "intent": "display.back"}
        if not self._session.answer:
            return None
        source_match = re.search(r"(?:ouvre|montre|lis)(?:-moi)?\s+(?:la\s+)?source\s+(\d+)", normalized)
        if source_match:
            index = int(source_match.group(1)) - 1
            sources = self._session.answer.sources
            if 0 <= index < len(sources):
                self.publish("voice.display.view.opened", {"view": "source", "source_id": sources[index].id, "index": index})
                return {"reply": f"Source {index + 1} affichée.", "intent": "source.open", "index": index}
            return {"reply": "Cette source n'est pas disponible.", "intent": "source.unavailable"}
        if normalized in {"source suivante", "source precedente"}:
            delta = -1 if "preced" in normalized else 1
            sources = self._session.answer.sources
            if not sources:
                return {"reply": "Aucune source externe n'est disponible.", "intent": "source.none"}
            current = int((self._session.current_focus or {}).get("index", -1))
            index = (current + delta) % len(sources)
            self.publish("voice.display.focus.changed", {"view": "source", "source_id": sources[index].id, "index": index})
            return {"reply": f"Source {index + 1} sélectionnée.", "intent": "source.focus", "index": index}
        if normalized in {"suivant", "precedent"}:
            delta = -1 if normalized == "precedent" else 1
            results = next(
                (
                    list(section.data.get("items") or [])
                    for section in self._session.answer.sections
                    if section.type in {"ranked_results", "email"}
                ),
                [],
            )
            if not results:
                return None
            current = int((self._session.current_focus or {}).get("index", -1))
            index = (current + delta) % len(results)
            self.publish("voice.display.focus.changed", {"view": "result", "index": index, "item_id": f"result-{index + 1}"})
            return {"reply": f"Résultat {index + 1} sélectionné.", "intent": "result.focus", "index": index}
        result_match = re.search(r"(?:ouvre|montre|detaille).*(?:numero\s+)?(\d+|premier|deuxieme|second|troisieme)", normalized)
        if result_match:
            raw = result_match.group(1)
            words = {"premier": 1, "deuxieme": 2, "second": 2, "troisieme": 3}
            index = words.get(raw, int(raw) if raw.isdigit() else 1) - 1
            results = next(
                (
                    list(section.data.get("items") or [])
                    for section in self._session.answer.sections
                    if section.type in {"ranked_results", "email"}
                ),
                [],
            )
            if index < 0 or index >= len(results):
                return {"reply": "Ce résultat n'est pas disponible.", "intent": "result.unavailable"}
            self.publish("voice.display.focus.changed", {"view": "result", "index": index, "item_id": f"result-{index + 1}"})
            return {"reply": f"Résultat {index + 1} sélectionné.", "intent": "result.focus", "index": index}
        return None


_COORDINATORS: dict[str, VoiceDisplayCoordinator] = {}
_COORDINATORS_LOCK = threading.RLock()


def get_voice_display_coordinator(profile_id: str | None = None) -> VoiceDisplayCoordinator:
    """Retourne le coordinateur HUD isolé pour le profil demandé ou actif."""
    from database.core import current_profile_id, normalize_profile_id

    selected = normalize_profile_id(profile_id or current_profile_id())
    with _COORDINATORS_LOCK:
        coordinator = _COORDINATORS.get(selected)
        if coordinator is None:
            coordinator = VoiceDisplayCoordinator()
            _COORDINATORS[selected] = coordinator
        return coordinator


def reset_all_voice_displays() -> None:
    """Réinitialise tous les coordinateurs HUD (tests uniquement)."""
    with _COORDINATORS_LOCK:
        for coordinator in _COORDINATORS.values():
            for subscription in tuple(coordinator._subscriptions):
                coordinator.unsubscribe(subscription)
            coordinator.reset()
        _COORDINATORS.clear()


class ProfileScopedVoiceDisplay:
    """Facade qui isole le HUD par profil utilisateur."""

    @staticmethod
    def enabled() -> bool:
        return VoiceDisplayCoordinator.enabled()

    def _coord(self, profile_id: str | None = None) -> VoiceDisplayCoordinator:
        return get_voice_display_coordinator(profile_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._coord(), name)


voice_display = ProfileScopedVoiceDisplay()


def handle_voice_display_command(text: str) -> dict[str, Any] | None:
    return get_voice_display_coordinator().navigation_command(text)


__all__ = [
    "ClaimEvidence", "SCHEMA_VERSION", "SourceEvidence", "SpeechSegment", "VisualAnswer",
    "VisualSection", "VoiceAction", "VoiceDisplayEvent", "VoiceDisplaySession",
    "VoiceDisplaySnapshot", "ProfileScopedVoiceDisplay", "answer_from_result",
    "get_voice_display_coordinator", "handle_voice_display_command",
    "reset_all_voice_displays", "voice_display",
]

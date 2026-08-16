"""Contrats publics de la recherche de connaissances JARVIS.

Le profil n'apparait volontairement dans aucun contrat : la base active est
selectionnee par ``database.use_profile`` avant l'appel au retrieval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


RetrievalStatus = Literal["ok", "degraded", "unavailable"]

_CONTEXT_REFERENCE_RE = re.compile(
    r"\b(?:ce|cet|cette|ces|celui|celle|ceux|celles|ça|cela|il|elle|ils|elles)\b"
    r"|\bdont\s+(?:je|on|nous)\s+(?:parlais|parlait|parlions)\b",
    re.IGNORECASE,
)

CANONICAL_SOURCE_TYPES = frozenset(
    {
        "conversation",
        "message",
        "email",
        "calendar",
        "imessage",
        "notification",
        "recording",
        "conversation_turn",
        "episode",
        "note",
        "journal",
        "fact",
        "life_context",
        "pattern",
        "insight",
        "briefing",
        "commitment",
        "location",
        "wellbeing",
        "activity",
        "document",
        "school_document",
        "conversation_document",
        "person",
        "people_event",
        "relationship",
        "relationship_event",
        "task",
        "control_task",
        "control_plan",
        "control_comment",
        "control_report",
        "control_activity",
        "project",
        "agent_run",
        "agent_step",
        "agent_approval",
        "agent_artifact",
        "agentic_workflow",
        "cursor_job",
        "scheduler_job",
        "work_session",
    }
)


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    """Demande bornee de recherche multi-source."""

    query: str
    conversation_id: int | None = None
    recent_user_turns: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    interaction_mode: str = "chat"
    source_types: tuple[str, ...] = ()
    person: str | None = None
    from_iso: str | None = None
    to_iso: str | None = None
    max_candidates: int = 20
    max_hits: int = 8
    char_budget: int = 8_000

    def __post_init__(self) -> None:
        query = " ".join(str(self.query or "").strip().split())[:1_000]
        turns = tuple(
            " ".join(str(turn).strip().split())[:1_000]
            for turn in tuple(self.recent_user_turns or ())[-6:]
            if str(turn).strip()
        )
        entities = tuple(
            dict.fromkeys(
                " ".join(str(entity).strip().split())[:240]
                for entity in tuple(self.entities or ())[:20]
                if str(entity).strip()
            )
        )
        sources = tuple(
            dict.fromkeys(str(item).strip() for item in self.source_types if item)
        )
        unknown = sorted(set(sources) - CANONICAL_SOURCE_TYPES)
        if unknown:
            raise ValueError(f"unknown_source_type:{','.join(unknown)}")

        person = " ".join(str(self.person or "").strip().split())[:240] or None
        mode = str(self.interaction_mode or "chat").strip().lower()[:40] or "chat"
        conversation_id = self.conversation_id
        if conversation_id is not None:
            conversation_id = int(conversation_id)
            if conversation_id <= 0:
                conversation_id = None

        object.__setattr__(self, "query", query)
        object.__setattr__(self, "recent_user_turns", turns)
        object.__setattr__(self, "entities", entities)
        object.__setattr__(self, "source_types", sources)
        object.__setattr__(self, "person", person)
        object.__setattr__(self, "interaction_mode", mode)
        object.__setattr__(self, "conversation_id", conversation_id)
        object.__setattr__(self, "from_iso", _clean_iso(self.from_iso))
        object.__setattr__(self, "to_iso", _clean_iso(self.to_iso))
        object.__setattr__(
            self, "max_candidates", max(1, min(20, int(self.max_candidates)))
        )
        object.__setattr__(self, "max_hits", max(1, min(8, int(self.max_hits))))
        object.__setattr__(
            self, "char_budget", max(256, min(8_000, int(self.char_budget)))
        )

    @property
    def effective_query(self) -> str:
        """Requete enrichie par un historique utilisateur tres borne."""

        parts = [self.query] if self.query else []
        parts.extend(self.entities)
        if _CONTEXT_REFERENCE_RE.search(self.query):
            parts.extend(self.recent_user_turns)
        return " ".join(dict.fromkeys(part for part in parts if part))[:4_000]

    @property
    def uses_context_reference(self) -> bool:
        return bool(_CONTEXT_REFERENCE_RE.search(self.query))

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "conversation_id": self.conversation_id,
            "recent_user_turns": list(self.recent_user_turns),
            "entities": list(self.entities),
            "interaction_mode": self.interaction_mode,
            "source_types": list(self.source_types),
            "person": self.person,
            "from_iso": self.from_iso,
            "to_iso": self.to_iso,
            "max_candidates": self.max_candidates,
            "max_hits": self.max_hits,
            "char_budget": self.char_budget,
        }


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    """Resultat homogene, tracable et considere comme donnee non fiable."""

    uid: str
    source_type: str
    source_id: str
    title: str
    excerpt: str
    content: str | None = None
    conversation_id: int | None = None
    occurred_at: str | None = None
    source_updated_at: str | None = None
    indexed_at: str | None = None
    score: float = 0.0
    reasons: tuple[str, ...] = ()
    trust: str = "untrusted_stored_data"
    sensitivity: str = "personal"
    cloud_policy: str = "redact"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def provenance(self) -> Mapping[str, str]:
        return {
            "uid": self.uid,
            "source_type": self.source_type,
            "source_id": self.source_id,
        }

    @property
    def confidence(self) -> float:
        positive = max(0.0, float(self.score))
        return round(positive / (positive + 5.0), 4) if positive else 0.0

    @property
    def freshness_at(self) -> str | None:
        return self.source_updated_at or self.occurred_at or self.indexed_at

    def as_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "title": self.title,
            "excerpt": self.excerpt,
            "content": self.content,
            "conversation_id": self.conversation_id,
            "occurred_at": self.occurred_at,
            "source_updated_at": self.source_updated_at,
            "indexed_at": self.indexed_at,
            "score": self.score,
            "confidence": self.confidence,
            "freshness_at": self.freshness_at,
            "provenance": dict(self.provenance),
            "reasons": list(self.reasons),
            "trust": self.trust,
            "sensitivity": self.sensitivity,
            "cloud_policy": self.cloud_policy,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Resultat global distinguant absence verifiee et source indisponible."""

    status: RetrievalStatus
    query: str
    hits: tuple[RetrievalHit, ...] = ()
    candidate_count: int = 0
    verified_sources: tuple[str, ...] = ()
    unavailable_sources: tuple[str, ...] = ()
    index_freshness_at: str | None = None
    index_lag_seconds: float | None = None
    latency_ms: float | None = None
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"ok", "degraded", "unavailable"}:
            raise ValueError(f"invalid_retrieval_status:{self.status}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "query": self.query,
            "hits": [hit.as_dict() for hit in self.hits],
            "candidate_count": self.candidate_count,
            "verified_sources": list(self.verified_sources),
            "unavailable_sources": list(self.unavailable_sources),
            "index_freshness_at": self.index_freshness_at,
            "index_lag_seconds": self.index_lag_seconds,
            "latency_ms": self.latency_ms,
            "diagnostics": list(self.diagnostics),
        }


def _clean_iso(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()[:64]
    return cleaned or None

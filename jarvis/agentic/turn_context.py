"""Snapshot borne du contexte de connaissance d'un tour conversationnel.

Le snapshot relie le chat, la planification et le runtime agentique sans
relancer silencieusement une recherche.  Il ne contient que la tranche de
connaissance deja bornee et marquee comme non fiable, ses references opaques
et un historique conversationnel nettoye.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from database.core import current_profile_id
from jarvis.security.llm_data_boundary import (
    sanitize_history_messages,
    wrap_untrusted_data,
)


SNAPSHOT_METADATA_KEY = "turn_knowledge_snapshot"
AGENTIC_ROUTING_METADATA_KEY = "agentic_routing"
SNAPSHOT_VERSION = 1
_MAX_RETRIEVAL_CHARS = 8_000
_MAX_REFERENCES = 8
_MAX_HISTORY_MESSAGES = 6
_MAX_HISTORY_CHARS = 1_000


def _bounded(value: Any, max_chars: int) -> str:
    return " ".join(str(value or "").split())[:max_chars]


def _untrusted_retrieval(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "[UNTRUSTED_DATA:" in text:
        return text[:_MAX_RETRIEVAL_CHARS]
    return wrap_untrusted_data(
        "KNOWLEDGE_RETRIEVAL",
        text,
        max_chars=_MAX_RETRIEVAL_CHARS,
    )


def _safe_status(value: Any) -> dict[str, Any]:
    source = dict(value) if isinstance(value, Mapping) else {}
    status = str(source.get("status") or "unavailable")
    if status not in {"ok", "degraded", "unavailable"}:
        status = "unavailable"
    safe: dict[str, Any] = {"status": status}
    for key in (
        "verified_sources",
        "partial_sources",
        "unavailable_sources",
        "diagnostics",
    ):
        raw = source.get(key)
        if isinstance(raw, (list, tuple, set, frozenset)):
            safe[key] = [_bounded(item, 160) for item in list(raw)[:40] if item]
        else:
            safe[key] = []
    for key in ("latency_ms", "index_lag_seconds"):
        raw = source.get(key)
        safe[key] = raw if isinstance(raw, (int, float)) else None
    safe["index_freshness_at"] = _bounded(source.get("index_freshness_at"), 64) or None
    return safe


def _safe_references(value: Any) -> tuple[Mapping[str, str], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    references: list[Mapping[str, str]] = []
    allowed = {
        "uid": 512,
        "reference": 512,
        "source_type": 80,
        "source_id": 512,
        "canonical_id": 512,
        "id": 512,
    }
    for item in list(value)[:_MAX_REFERENCES]:
        if not isinstance(item, Mapping):
            continue
        reference = {
            key: _bounded(item.get(key), limit)
            for key, limit in allowed.items()
            if item.get(key) not in (None, "")
        }
        if reference:
            references.append(MappingProxyType(reference))
    return tuple(references)


def _safe_live_status(value: Any) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        return MappingProxyType({})
    allowed = {"ok", "degraded", "unavailable", "skipped", "cached"}
    result = {
        _bounded(key, 80): status
        for key, raw_status in list(value.items())[:20]
        if (status := _bounded(raw_status, 40)) in allowed
    }
    return MappingProxyType(result)


@dataclass(frozen=True, slots=True)
class TurnKnowledgeSnapshot:
    """Contexte de connaissance immuable et reutilisable pour un seul tour."""

    snapshot_id: str
    profile_id: str
    conversation_id: str | None
    query: str
    interaction_mode: str
    created_at: str
    retrieval_context: str = ""
    retrieval_status: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({"status": "unavailable"})
    )
    retrieval_references: tuple[Mapping[str, str], ...] = ()
    conversation_history: tuple[Mapping[str, str], ...] = ()
    live_status: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    _enriched_context: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), repr=False, compare=False
    )

    @classmethod
    def capture(
        cls,
        *,
        query: str,
        conversation_id: int | str | None,
        interaction_mode: str,
        context: Mapping[str, Any],
        snapshot_id: str | None = None,
        profile_id: str | None = None,
    ) -> "TurnKnowledgeSnapshot":
        existing = context.get("__turn_knowledge_snapshot")
        selected_profile = str(profile_id or current_profile_id())
        if isinstance(existing, cls):
            if existing.profile_id != selected_profile:
                raise PermissionError("turn_snapshot_cross_profile")
            return existing
        history = sanitize_history_messages(
            context.get("history"),
            max_messages=_MAX_HISTORY_MESSAGES,
            max_chars_per_message=_MAX_HISTORY_CHARS,
        )
        return cls(
            snapshot_id=snapshot_id or f"turn_{uuid4().hex}",
            profile_id=selected_profile,
            conversation_id=(
                _bounded(conversation_id, 128) if conversation_id is not None else None
            ),
            query=_bounded(query, 1_000),
            interaction_mode=(
                interaction_mode
                if interaction_mode in {"chat", "voice", "stream", "agentic"}
                else "chat"
            ),
            created_at=datetime.now(UTC).isoformat(),
            retrieval_context=_untrusted_retrieval(context.get("retrieval_context")),
            retrieval_status=MappingProxyType(_safe_status(context.get("__retrieval"))),
            retrieval_references=_safe_references(
                context.get("__retrieval_references")
            ),
            conversation_history=tuple(
                MappingProxyType(
                    {
                        "role": _bounded(item.get("role"), 20),
                        "content": _bounded(item.get("content"), _MAX_HISTORY_CHARS),
                    }
                )
                for item in history
            ),
            live_status=_safe_live_status(context.get("__retrieval_live")),
            _enriched_context=MappingProxyType(dict(context)),
        )

    @classmethod
    def from_metadata(
        cls,
        value: Any,
        *,
        expected_profile_id: str,
    ) -> "TurnKnowledgeSnapshot | None":
        if not isinstance(value, Mapping):
            return None
        if int(value.get("version") or 0) != SNAPSHOT_VERSION:
            return None
        profile_id = _bounded(value.get("profile_id"), 128)
        if not profile_id or profile_id != expected_profile_id:
            raise PermissionError("turn_snapshot_cross_profile")
        snapshot_id = _bounded(value.get("snapshot_id"), 128)
        if not snapshot_id:
            return None
        snapshot = cls(
            snapshot_id=snapshot_id,
            profile_id=profile_id,
            conversation_id=_bounded(value.get("conversation_id"), 128) or None,
            query=_bounded(value.get("query"), 1_000),
            interaction_mode=_bounded(value.get("interaction_mode"), 40) or "agentic",
            created_at=_bounded(value.get("created_at"), 64),
            retrieval_context=_untrusted_retrieval(value.get("retrieval_context")),
            retrieval_status=MappingProxyType(
                _safe_status(value.get("retrieval_status"))
            ),
            retrieval_references=_safe_references(value.get("retrieval_references")),
            conversation_history=tuple(
                MappingProxyType(
                    {
                        "role": _bounded(item.get("role"), 20),
                        "content": _bounded(item.get("content"), _MAX_HISTORY_CHARS),
                    }
                )
                for item in list(value.get("conversation_history") or [])[
                    :_MAX_HISTORY_MESSAGES
                ]
                if isinstance(item, Mapping)
            ),
            live_status=_safe_live_status(value.get("live_status")),
        )
        return snapshot

    def to_context(self) -> dict[str, Any]:
        context = dict(self._enriched_context)
        if not context:
            context = {
                "retrieval_context": self.retrieval_context,
                "history": [dict(item) for item in self.conversation_history],
                "__retrieval": dict(self.retrieval_status),
                "__retrieval_references": [
                    dict(item) for item in self.retrieval_references
                ],
                "__retrieval_live": dict(self.live_status),
                "__retrieval_done": True,
            }
        context["__turn_knowledge_snapshot"] = self
        context["__turn_snapshot_id"] = self.snapshot_id
        return context

    def to_metadata(self) -> dict[str, Any]:
        return {
            "version": SNAPSHOT_VERSION,
            "snapshot_id": self.snapshot_id,
            "profile_id": self.profile_id,
            "conversation_id": self.conversation_id,
            "query": self.query,
            "interaction_mode": self.interaction_mode,
            "created_at": self.created_at,
            "retrieval_context": self.retrieval_context,
            "retrieval_status": dict(self.retrieval_status),
            "retrieval_references": [dict(item) for item in self.retrieval_references],
            "conversation_history": [dict(item) for item in self.conversation_history],
            "live_status": dict(self.live_status),
        }

    def agentic_context(self) -> dict[str, Any]:
        selected: dict[str, Any] = {}
        if self.retrieval_context:
            selected["retrieval_context"] = self.retrieval_context
        selected["retrieval_status"] = dict(self.retrieval_status)
        selected["retrieval_references"] = [
            dict(item) for item in self.retrieval_references
        ]
        if self.conversation_history:
            selected["conversation_history"] = [
                dict(item) for item in self.conversation_history
            ]
        selected["turn_snapshot_id"] = self.snapshot_id
        return selected

    def planning_context(self) -> dict[str, Any]:
        return {
            "retrieval_context": self.retrieval_context,
            "retrieval_status": dict(self.retrieval_status),
            "turn_snapshot_id": self.snapshot_id,
        }

    def public_payload(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "status": self.retrieval_status.get("status", "unavailable"),
            "verified_sources": list(
                self.retrieval_status.get("verified_sources") or []
            ),
            "partial_sources": list(self.retrieval_status.get("partial_sources") or []),
            "unavailable_sources": list(
                self.retrieval_status.get("unavailable_sources") or []
            ),
            "freshness_at": self.retrieval_status.get("index_freshness_at"),
            "latency_ms": self.retrieval_status.get("latency_ms"),
            "references": [dict(item) for item in self.retrieval_references],
        }


def snapshot_from_context(
    context: Mapping[str, Any] | None,
) -> TurnKnowledgeSnapshot | None:
    if not isinstance(context, Mapping):
        return None
    value = context.get("__turn_knowledge_snapshot")
    return value if isinstance(value, TurnKnowledgeSnapshot) else None


def public_knowledge_payload(context: Mapping[str, Any] | None) -> dict[str, Any]:
    snapshot = snapshot_from_context(context)
    if snapshot is not None:
        return snapshot.public_payload()
    if not isinstance(context, Mapping):
        return {}
    retrieval = _safe_status(context.get("__retrieval"))
    if not context.get("__retrieval_done") and not context.get("retrieval_context"):
        return {}
    return {
        "snapshot_id": context.get("__turn_snapshot_id"),
        "status": retrieval["status"],
        "verified_sources": retrieval["verified_sources"],
        "partial_sources": retrieval["partial_sources"],
        "unavailable_sources": retrieval["unavailable_sources"],
        "freshness_at": retrieval["index_freshness_at"],
        "latency_ms": retrieval["latency_ms"],
        "references": [
            dict(item)
            for item in list(context.get("__retrieval_references") or [])[:8]
            if isinstance(item, Mapping)
        ],
    }


__all__ = [
    "AGENTIC_ROUTING_METADATA_KEY",
    "SNAPSHOT_METADATA_KEY",
    "TurnKnowledgeSnapshot",
    "public_knowledge_payload",
    "snapshot_from_context",
]

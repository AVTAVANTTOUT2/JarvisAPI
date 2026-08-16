"""Registre explicite des adaptateurs vers les sources de verite."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Mapping, Protocol, Sequence

from .models import CANONICAL_SOURCE_TYPES, RetrievalRequest


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    source_type: str
    source_id: str
    title: str
    searchable_text: str
    summary: str = ""
    chunk_index: int = 0
    conversation_id: int | None = None
    people: tuple[str, ...] = ()
    occurred_at: str | None = None
    source_updated_at: str | None = None
    sensitivity: str = "personal"
    cloud_policy: str = "redact"
    trust: str = "untrusted_stored_data"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    indexable: bool = True

    @property
    def uid(self) -> str:
        return f"{self.source_type}:{self.source_id}:{max(0, int(self.chunk_index))}"


class RetrievalAdapter(Protocol):
    key: str
    source_type: str

    def search(
        self, request: RetrievalRequest, limit: int
    ) -> list[KnowledgeDocument]: ...

    def get(self, source_id: str) -> KnowledgeDocument | None: ...

    def iter_batch(
        self, cursor: str | None, limit: int
    ) -> tuple[list[KnowledgeDocument], str | None]: ...


class AdapterRegistry:
    """Collection immuable en pratique, interrogeable par source canonique."""

    def __init__(self, adapters: Sequence[RetrievalAdapter] = ()) -> None:
        self._adapters: list[RetrievalAdapter] = []
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: RetrievalAdapter) -> None:
        if adapter.source_type not in CANONICAL_SOURCE_TYPES:
            raise ValueError(f"unknown_adapter_source:{adapter.source_type}")
        if any(existing.key == adapter.key for existing in self._adapters):
            raise ValueError(f"duplicate_adapter_key:{adapter.key}")
        self._adapters.append(adapter)

    def adapters_for(
        self, source_types: Sequence[str] = ()
    ) -> tuple[RetrievalAdapter, ...]:
        selected = set(source_types or CANONICAL_SOURCE_TYPES)
        return tuple(
            adapter for adapter in self._adapters if adapter.source_type in selected
        )

    def adapters_for_source(self, source_type: str) -> tuple[RetrievalAdapter, ...]:
        return tuple(
            adapter for adapter in self._adapters if adapter.source_type == source_type
        )

    @property
    def source_types(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(adapter.source_type for adapter in self._adapters))

    @property
    def adapters(self) -> tuple[RetrievalAdapter, ...]:
        return tuple(self._adapters)


_default_registry: AdapterRegistry | None = None
_registry_lock = Lock()


def get_default_registry() -> AdapterRegistry:
    global _default_registry
    if _default_registry is None:
        with _registry_lock:
            if _default_registry is None:
                from .adapters import build_default_adapters

                _default_registry = AdapterRegistry(build_default_adapters())
    return _default_registry


def reset_default_registry() -> None:
    """Reserve aux tests qui changent de base ou de schema en cours de processus."""

    global _default_registry
    with _registry_lock:
        _default_registry = None

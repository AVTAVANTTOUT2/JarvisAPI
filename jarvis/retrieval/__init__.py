"""API publique de la memoire universelle JARVIS."""

from .coordinator import (
    backfill_knowledge,
    get_knowledge_item,
    process_knowledge_embeddings,
    process_knowledge_jobs,
    rebuild_knowledge_index,
    search_knowledge,
)
from .formatting import format_retrieval_context
from .models import (
    CANONICAL_SOURCE_TYPES,
    CoverageStatus,
    RetrievalHit,
    RetrievalRequest,
    RetrievalResult,
    RetrievalStatus,
    SourceCoverage,
)

__all__ = [
    "CANONICAL_SOURCE_TYPES",
    "CoverageStatus",
    "RetrievalHit",
    "RetrievalRequest",
    "RetrievalResult",
    "RetrievalStatus",
    "SourceCoverage",
    "backfill_knowledge",
    "format_retrieval_context",
    "get_knowledge_item",
    "process_knowledge_embeddings",
    "process_knowledge_jobs",
    "rebuild_knowledge_index",
    "search_knowledge",
]

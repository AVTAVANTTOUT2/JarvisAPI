"""Construction du contexte borné transmis au runtime agentique."""

from __future__ import annotations

from typing import Any

from jarvis.security.llm_data_boundary import sanitize_history_messages


def agentic_memory_context(enriched_context: dict[str, Any] | None) -> dict[str, Any]:
    """Réduit le contexte conversationnel aux données utiles et déjà bornées."""

    if not enriched_context:
        return {}
    selected: dict[str, Any] = {}
    retrieval_context = enriched_context.get("retrieval_context")
    if retrieval_context:
        selected["retrieval_context"] = retrieval_context
    references = enriched_context.get("__retrieval_references")
    if isinstance(references, list):
        selected["retrieval_references"] = references[:8]
    retrieval_meta = enriched_context.get("__retrieval")
    if isinstance(retrieval_meta, dict):
        selected["retrieval_status"] = retrieval_meta
    history = sanitize_history_messages(
        enriched_context.get("history"),
        max_messages=6,
        max_chars_per_message=1_000,
    )
    if history:
        selected["conversation_history"] = history
    return selected

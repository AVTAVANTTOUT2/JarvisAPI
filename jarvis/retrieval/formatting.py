"""Formatage borne et explicitement non fiable du contexte de retrieval."""

from __future__ import annotations

import json
from typing import Any

from jarvis.security.llm_data_boundary import (
    redact_for_external_llm,
    wrap_untrusted_data,
)

from .models import RetrievalResult


def format_retrieval_context(
    result: RetrievalResult,
    max_chars: int = 8_000,
) -> str:
    """Produit le seul bloc de contexte destine au LLM, avec provenance.

    Les corps complets ne sont jamais inclus ici : seuls les extraits deja bornes
    par le coordinateur sont serialises. Un corps peut uniquement etre hydrate
    explicitement via ``get_knowledge_item``.
    """

    if not isinstance(result, RetrievalResult):
        raise TypeError("format_retrieval_context attend RetrievalResult")
    limit = max(256, min(8_000, int(max_chars)))
    payload = {
        "status": result.status,
        "query": _redacted(result.query, max_chars=800),
        "candidate_count": result.candidate_count,
        "verified_sources": list(result.verified_sources),
        "partial_sources": list(result.partial_sources),
        "unavailable_sources": list(result.unavailable_sources),
        "source_coverage": [
            coverage.as_dict()
            for coverage in result.source_coverage
            if coverage.status != "complete"
        ],
        "index_freshness_at": result.index_freshness_at,
        "index_lag_seconds": result.index_lag_seconds,
        "latency_ms": result.latency_ms,
        "hits": [_format_hit(hit) for hit in result.hits],
    }
    inner_limit = max(64, limit - 128)
    for _ in range(12):
        serialized = _bounded_json(payload, inner_limit)
        wrapped = wrap_untrusted_data(
            "KNOWLEDGE_RETRIEVAL",
            serialized,
            # Tous les champs dynamiques sont déjà redacted. Cette marge évite
            # qu'un second passage coupe le document JSON interne.
            max_chars=max(512, len(serialized) * 3),
        )
        if len(wrapped) <= limit:
            return wrapped
        overflow = len(wrapped) - limit
        next_limit = max(64, inner_limit - overflow - 16)
        if next_limit == inner_limit:
            break
        inner_limit = next_limit

    serialized = _bounded_json(
        {
            "status": result.status,
            "candidate_count": result.candidate_count,
            "partial_sources": list(result.partial_sources),
            "truncated": True,
            "hits": [],
        },
        96,
    )
    return wrap_untrusted_data(
        "KNOWLEDGE_RETRIEVAL",
        serialized,
        max_chars=512,
    )


def _format_hit(hit: object) -> dict[str, object]:
    if getattr(hit, "cloud_policy") == "local_only":
        return {
            "uid": getattr(hit, "uid"),
            "source_type": getattr(hit, "source_type"),
            "cloud_policy": "local_only",
            "content": "[CONTENU LOCAL UNIQUEMENT]",
        }
    payload: dict[str, object] = {
        "uid": getattr(hit, "uid"),
        "source_type": getattr(hit, "source_type"),
        "source_id": _redacted(getattr(hit, "source_id"), max_chars=400),
        "title": _redacted(getattr(hit, "title"), max_chars=400),
        "occurred_at": getattr(hit, "occurred_at"),
        "freshness_at": getattr(hit, "freshness_at"),
        "score": getattr(hit, "score"),
        "confidence": getattr(hit, "confidence"),
        "reasons": list(getattr(hit, "reasons")),
        "trust": getattr(hit, "trust"),
    }
    payload["excerpt"] = _redacted(getattr(hit, "excerpt"), max_chars=2_000)
    return payload


def _redacted(value: object, *, max_chars: int) -> str:
    """Redacte les champs textuels avant leur sérialisation JSON.

    La frontière globale redéfait volontairement la même opération (secrets
    uniquement) : le JSON interne ne peut plus être coupé au milieu d'une chaîne.
    """

    return redact_for_external_llm(value, max_chars=max_chars)


def _json_dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _bounded_json(payload: dict[str, Any], limit: int) -> str:
    """Réduit le contenu par unités JSON et garantit un document décodable."""

    serialized = _json_dump(payload)
    if len(serialized) <= limit:
        return serialized

    hits: list[dict[str, Any]] = payload["hits"]
    for excerpt_limit in (640, 320, 160, 80):
        for hit in hits:
            excerpt = str(hit.get("excerpt") or "")
            if len(excerpt) > excerpt_limit:
                hit["excerpt"] = excerpt[: max(0, excerpt_limit - 1)] + "…"
        serialized = _json_dump(payload)
        if len(serialized) <= limit:
            return serialized

    payload["query"] = str(payload.get("query") or "")[:160]
    for key in ("verified_sources", "unavailable_sources"):
        payload[key] = list(payload.get(key) or [])[:12]

    while hits:
        serialized = _json_dump(payload)
        if len(serialized) <= limit:
            return serialized
        if len(hits) == 1:
            hit = hits[0]
            for optional_key in (
                "reasons",
                "trust",
                "confidence",
                "freshness_at",
                "occurred_at",
                "score",
                "source_id",
                "title",
            ):
                hit.pop(optional_key, None)
                serialized = _json_dump(payload)
                if len(serialized) <= limit:
                    return serialized
            break
        hits.pop()

    compact = {
        "status": payload["status"],
        "candidate_count": payload["candidate_count"],
        "truncated": True,
        "hits": [
            {
                "uid": hit.get("uid"),
                "source_type": hit.get("source_type"),
            }
            for hit in hits[:1]
        ],
    }
    serialized = _json_dump(compact)
    if len(serialized) <= limit:
        return serialized
    return _json_dump({"status": payload["status"], "truncated": True})

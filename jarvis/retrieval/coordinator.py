"""Coordination synchrone et bornee du retrieval multi-source."""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from database.core import db_transaction
from database.knowledge import (
    claim_knowledge_jobs,
    complete_knowledge_job,
    delete_knowledge_item,
    delete_knowledge_sources,
    fail_knowledge_job,
    get_knowledge_embeddings,
    get_knowledge_item_row,
    get_missing_knowledge_embeddings,
    get_recent_knowledge_references,
    get_knowledge_source_states,
    knowledge_job_claim_is_current,
    latest_knowledge_indexed_at,
    search_knowledge_items,
    save_knowledge_references,
    update_knowledge_source_state,
    upsert_knowledge_embedding,
    upsert_knowledge_item,
)
from database.time_buckets import (
    SQLITE_UTC_FORMAT,
    local_datetime,
    sqlite_utc_timestamp,
    utc_bounds_for_local_dates,
    utc_bounds_for_local_day,
)

from .models import RetrievalHit, RetrievalRequest, RetrievalResult
from .registry import AdapterRegistry, KnowledgeDocument, get_default_registry


logger = logging.getLogger(__name__)
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_MAIL_SOURCE_RE = re.compile(
    r"\b(?:mail|mails|e-?mail|emails|courriel|courriels|bo[iî]te)\b",
    re.IGNORECASE,
)
_CHRONOLOGICAL_RE = re.compile(
    r"\b(?:dernier|derniers|derni[eè]re|derni[eè]res|r[eé]cent|r[eé]cents|latest|last)\b",
    re.IGNORECASE,
)
_SUMMARY_RE = re.compile(r"\b(?:r[eé]sum|synth[eè]s)", re.IGNORECASE)
_SOURCE_INTENTS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (_MAIL_SOURCE_RE, ("email",)),
    (
        re.compile(
            r"\b(?:agenda|calendrier|calendar|rendez-vous|[eé]v[eé]nement)\b", re.I
        ),
        ("calendar",),
    ),
    (
        re.compile(
            r"\b(?:note\s+vocale|notes\s+vocales|transcription|enregistrement)\b", re.I
        ),
        ("recording", "conversation_turn"),
    ),
    (
        re.compile(
            r"\b(?:imessage|sms|message|messages|conversation|conversations)\b", re.I
        ),
        ("imessage", "notification", "message", "conversation"),
    ),
    (
        re.compile(r"\b(?:document|documents|fichier|fichiers|cours)\b", re.I),
        ("school_document", "conversation_document", "document"),
    ),
    (
        re.compile(r"\b(?:t[aâ]che|t[aâ]ches|todo|[aà]\s+faire)\b", re.I),
        (
            "task",
            "control_task",
            "control_plan",
            "control_comment",
            "control_report",
            "control_activity",
        ),
    ),
    (
        re.compile(
            r"\b(?:projet|projets|agent|agents|agentique|workflow|cursor|d[eé]l[eé]gation|job\s+planifi[eé])\b",
            re.I,
        ),
        (
            "project",
            "agent_run",
            "agent_step",
            "agent_approval",
            "agent_artifact",
            "agentic_workflow",
            "cursor_job",
            "scheduler_job",
            "work_session",
            "control_task",
            "control_plan",
            "control_comment",
            "control_report",
            "control_activity",
        ),
    ),
    (
        re.compile(r"\b(?:note|notes|journal|souvenir|souvenirs)\b", re.I),
        ("note", "episode", "journal", "fact", "life_context"),
    ),
    (
        re.compile(
            r"\b(?:humeur|bien[- ]?[eê]tre|repas|nutrition|fitness|poids|sport)\b", re.I
        ),
        ("wellbeing",),
    ),
    (
        re.compile(r"\b(?:lieu|lieux|visite|visites|localisation|adresse)\b", re.I),
        ("location",),
    ),
    (
        re.compile(r"\b(?:[eé]cran|usage|activit[eé]|pr[eé]sence)\b", re.I),
        ("activity", "work_session"),
    ),
    (
        re.compile(r"\b(?:personne|relation|relations|contact|contacts)\b", re.I),
        ("person", "people_event", "relationship", "relationship_event"),
    ),
)
_SCORING_STOPWORDS = frozenset(
    {
        "a",
        "ai",
        "au",
        "aux",
        "ce",
        "ces",
        "dans",
        "de",
        "des",
        "du",
        "en",
        "est",
        "et",
        "il",
        "je",
        "la",
        "le",
        "les",
        "lire",
        "lis",
        "lises",
        "mail",
        "mails",
        "email",
        "emails",
        "me",
        "mes",
        "mon",
        "ne",
        "pas",
        "que",
        "qui",
        "résume",
        "resume",
        "résumer",
        "resumer",
        "tu",
        "un",
        "une",
        "veux",
        "veut",
        "voudrais",
        "donne",
        "montre",
        "trouve",
        "retrouve",
        "quoi",
        "passe",
        "passé",
        "arrive",
        "arrivé",
        "dernier",
        "derniers",
        "dernière",
        "dernières",
        "recent",
        "recents",
        "récent",
        "récents",
        "hier",
        "aujourd",
        "hui",
        "demain",
        "semaine",
        "avant",
        "deux",
        "trois",
        "quatre",
        "cinq",
        "six",
        "sept",
        "huit",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
    }
)


def search_knowledge(request: RetrievalRequest) -> RetrievalResult:
    """Recherche l'index et les sources canoniques sans jamais tout injecter."""

    started_at = time.perf_counter()
    if not isinstance(request, RetrievalRequest):
        raise TypeError("search_knowledge attend RetrievalRequest")
    request, implicit_period = _with_implicit_time_range(request)
    registry = get_default_registry()
    requested_sources = _requested_source_types(request, registry)
    diagnostics: list[str] = []
    if implicit_period:
        diagnostics.append(f"implicit_time_range:{implicit_period}")
    unavailable: set[str] = set()
    verified: set[str] = set()

    try:
        job_report = process_knowledge_jobs(limit=50, registry=registry)
        if job_report["failed"]:
            diagnostics.append(f"index_jobs_failed:{job_report['failed']}")
    except Exception as exc:
        logger.warning("[retrieval] traitement des jobs impossible", exc_info=True)
        diagnostics.append(f"index_jobs:{type(exc).__name__}")

    indexed_rows: list[dict[str, Any]] = []
    try:
        indexed_rows, backend = search_knowledge_items(
            request.effective_query,
            source_types=requested_sources,
            conversation_id=None,
            person=request.person,
            from_iso=request.from_iso,
            to_iso=request.to_iso,
            limit=request.max_candidates,
        )
        diagnostics.append(f"index_backend:{backend}")
    except Exception as exc:
        logger.warning("[retrieval] index local indisponible", exc_info=True)
        diagnostics.append(f"knowledge_index:{type(exc).__name__}")

    reference_uids: set[str] = set()
    if request.conversation_id is not None and request.uses_context_reference:
        try:
            references = get_recent_knowledge_references(
                request.conversation_id,
                limit=request.max_hits,
            )
            indexed_by_uid = {str(row["uid"]): row for row in indexed_rows}
            for reference in references:
                source_type = str(reference.get("source_type") or "")
                if source_type not in requested_sources:
                    continue
                uid = str(reference.get("uid") or "")
                row = get_knowledge_item_row(uid)
                reference_source_id = str(reference.get("source_id") or "")
                if (
                    row is None
                    or str(row.get("source_type") or "") != source_type
                    or str(row.get("source_id") or "") != reference_source_id
                    or str(row.get("source_type") or "") not in requested_sources
                    or not _index_row_matches_structured_filters(row, request)
                ):
                    continue
                indexed_by_uid[uid] = row
                reference_uids.add(uid)
            indexed_rows = list(indexed_by_uid.values())
            diagnostics.append(f"conversation_references:{len(reference_uids)}")
        except Exception as exc:
            diagnostics.append(f"conversation_reference_load:{type(exc).__name__}")

    documents: dict[str, KnowledgeDocument] = {}
    source_successes: dict[str, int] = {source: 0 for source in requested_sources}
    source_failures: dict[str, int] = {source: 0 for source in requested_sources}
    per_adapter = max(2, min(8, request.max_candidates))
    now = sqlite_utc_timestamp()

    for adapter in registry.adapters_for(requested_sources):
        try:
            found = adapter.search(request, per_adapter)
            source_successes[adapter.source_type] = (
                source_successes.get(adapter.source_type, 0) + 1
            )
            for document in found:
                documents[document.uid] = document
            _update_source_state_best_effort(
                diagnostics,
                adapter.key,
                adapter.source_type,
                status="ok",
                last_indexed_at=now,
                error_code=None,
            )
        except Exception as exc:
            source_failures[adapter.source_type] = (
                source_failures.get(adapter.source_type, 0) + 1
            )
            diagnostics.append(f"{adapter.key}:{type(exc).__name__}")
            _update_source_state_best_effort(
                diagnostics,
                adapter.key,
                adapter.source_type,
                status="unavailable",
                error_code=type(exc).__name__,
            )
            logger.debug(
                "[retrieval] source %s indisponible", adapter.key, exc_info=True
            )

    semantic_scores: dict[str, float] = {}
    if not _CHRONOLOGICAL_RE.search(request.query):
        semantic_rows, current_scores, current_diagnostic = _knowledge_semantic_rows(
            request,
            requested_sources,
        )
        if semantic_rows:
            indexed_by_uid = {str(row["uid"]): row for row in indexed_rows}
            indexed_by_uid.update({str(row["uid"]): row for row in semantic_rows})
            indexed_rows = list(indexed_by_uid.values())
        semantic_scores.update(current_scores)
        if current_diagnostic:
            diagnostics.append(current_diagnostic)

    if len(documents) < request.max_hits and not _CHRONOLOGICAL_RE.search(
        request.query
    ):
        semantic_documents, semantic_scores, semantic_diagnostic = (
            _legacy_semantic_documents(request, requested_sources, registry)
        )
        documents.update(semantic_documents)
        semantic_scores.update(current_scores)
        if semantic_diagnostic:
            diagnostics.append(semantic_diagnostic)

    candidates: dict[str, RetrievalHit] = {}
    for row in indexed_rows:
        hit = _hit_from_index_row(row, request)
        if hit.uid in reference_uids:
            hit = replace(
                hit,
                score=round(hit.score + 12.0, 4),
                reasons=tuple(dict.fromkeys((*hit.reasons, "conversation_reference"))),
            )
        semantic_score = semantic_scores.get(hit.uid)
        if semantic_score is not None:
            hit = replace(
                hit,
                score=round(hit.score + (semantic_score * 6.0), 4),
                reasons=tuple(dict.fromkeys((*hit.reasons, "semantic"))),
            )
        candidates[hit.uid] = hit
    for document in documents.values():
        hit = _hit_from_document(document, request)
        semantic_score = semantic_scores.get(document.uid)
        if semantic_score is not None:
            hit = replace(
                hit,
                score=round(hit.score + (semantic_score * 6.0), 4),
                reasons=tuple(dict.fromkeys((*hit.reasons, "semantic"))),
            )
        previous = candidates.get(hit.uid)
        if previous is None or hit.score >= previous.score:
            candidates[hit.uid] = hit

    candidates = {
        hit.uid: hit for hit in _deduplicate_canonical_hits(candidates.values())
    }

    # Les demandes « derniers N » sont strictement chronologiques : un bonus
    # lexical/FTS ne doit jamais faire remonter une ancienne archive.
    ranked = sorted(candidates.values(), key=lambda hit: hit.uid)
    ranked.sort(
        key=lambda hit: str(
            hit.occurred_at or hit.source_updated_at or hit.indexed_at or ""
        ),
        reverse=True,
    )
    if not _CHRONOLOGICAL_RE.search(request.query):
        ranked.sort(key=lambda hit: hit.score, reverse=True)
        ranked = _diversify_hits(ranked, request.max_hits)
    ranked = ranked[: request.max_candidates]

    for hit in ranked:
        document = documents.get(hit.uid)
        if document is None:
            continue
        try:
            _persist_document(document)
        except Exception as exc:
            diagnostics.append(f"persist:{document.source_type}:{type(exc).__name__}")

    requested_hit_count = _requested_hit_count(request)
    final_hits = _apply_result_budget(
        ranked,
        min(request.max_hits, requested_hit_count or request.max_hits),
        request.char_budget,
    )

    if request.conversation_id is not None and final_hits:
        try:
            save_knowledge_references(
                request.conversation_id,
                [hit.provenance for hit in final_hits],
            )
        except Exception as exc:
            diagnostics.append(f"conversation_reference_save:{type(exc).__name__}")

    for source in requested_sources:
        successes = source_successes.get(source, 0)
        failures = source_failures.get(source, 0)
        if successes:
            verified.add(source)
        if not successes or failures:
            unavailable.add(source)

    try:
        index_freshness_at = latest_knowledge_indexed_at(requested_sources)
    except Exception as exc:
        index_freshness_at = None
        diagnostics.append(f"index_freshness:{type(exc).__name__}")

    if not verified and not final_hits:
        status = "unavailable"
    elif unavailable or any(
        item.startswith(
            (
                "index_jobs",
                "knowledge_index",
                "persist:",
                "source_state:",
                "index_freshness:",
                "conversation_reference_load:",
                "conversation_reference_save:",
            )
        )
        for item in diagnostics
    ):
        status = "degraded"
    else:
        status = "ok"

    return RetrievalResult(
        status=status,
        query=request.query,
        hits=tuple(final_hits),
        candidate_count=len(candidates),
        verified_sources=tuple(sorted(verified)),
        unavailable_sources=tuple(sorted(unavailable)),
        index_freshness_at=index_freshness_at,
        index_lag_seconds=_index_lag_seconds(index_freshness_at),
        latency_ms=round((time.perf_counter() - started_at) * 1_000, 3),
        diagnostics=tuple(diagnostics),
    )


def _update_source_state_best_effort(
    diagnostics: list[str],
    source_key: str,
    source_type: str,
    **values: Any,
) -> None:
    try:
        update_knowledge_source_state(source_key, source_type, **values)
    except Exception as exc:
        diagnostics.append(f"source_state:{source_key}:{type(exc).__name__}")


def get_knowledge_item(uid: str, max_chars: int = 12_000) -> RetrievalHit | None:
    """Hydrate un UID opaque deja indexe, sans accepter de profil explicite."""

    identifier = str(uid or "").strip()
    if not identifier or len(identifier) > 500:
        return None
    limit = max(1, min(12_000, int(max_chars)))
    row = get_knowledge_item_row(identifier)
    if row is None:
        return None
    content = str(row.get("searchable_text") or "")[:limit]
    return RetrievalHit(
        uid=str(row["uid"]),
        source_type=str(row["source_type"]),
        source_id=str(row["source_id"]),
        title=str(row.get("title") or ""),
        excerpt=_excerpt(content, "", min(1_000, limit)),
        content=content,
        conversation_id=_optional_int(row.get("conversation_id")),
        occurred_at=_optional_text(row.get("occurred_at")),
        source_updated_at=_optional_text(row.get("source_updated_at")),
        indexed_at=_optional_text(row.get("indexed_at")),
        score=1.0,
        reasons=("hydrated",),
        trust=str(row.get("trust") or "untrusted_stored_data"),
        sensitivity=str(row.get("sensitivity") or "personal"),
        cloud_policy=str(row.get("cloud_policy") or "redact"),
        metadata=dict(row.get("metadata") or {}),
    )


def process_knowledge_jobs(
    limit: int = 100,
    *,
    registry: AdapterRegistry | None = None,
) -> dict[str, int]:
    """Draine un lot de hooks transactionnels, avec retry durable."""

    active_registry = registry or get_default_registry()
    jobs = claim_knowledge_jobs(limit)
    report = {"claimed": len(jobs), "indexed": 0, "deleted": 0, "failed": 0}
    for job in jobs:
        source_type = str(job["source_type"])
        source_id = str(job["source_id"])
        claim_token = str(job["updated_at"])
        try:
            if not knowledge_job_claim_is_current(int(job["id"]), claim_token):
                continue
            if str(job["operation"]) == "delete":
                applied, affected = _apply_claimed_projection(
                    int(job["id"]),
                    claim_token,
                    source_type,
                    source_id,
                    None,
                )
                if applied:
                    report["deleted"] += affected
                continue

            document = None
            adapters = active_registry.adapters_for_source(source_type)
            if not adapters:
                raise LookupError("adapter_missing")
            for adapter in adapters:
                document = adapter.get(source_id)
                if document is not None:
                    break
            applied, affected = _apply_claimed_projection(
                int(job["id"]),
                claim_token,
                source_type,
                source_id,
                document,
            )
            if not applied:
                continue
            if document is None or not document.indexable:
                report["deleted"] += affected
            else:
                report["indexed"] += 1
        except Exception as exc:
            report["failed"] += 1
            fail_knowledge_job(
                int(job["id"]),
                type(exc).__name__,
                claim_token=claim_token,
            )
            logger.debug("[retrieval] job %s echoue", job["id"], exc_info=True)
    return report


def _apply_claimed_projection(
    job_id: int,
    claim_token: str,
    source_type: str,
    source_id: str,
    document: KnowledgeDocument | None,
) -> tuple[bool, int]:
    """Fence la projection et la complétion du job dans une même écriture SQLite."""

    with db_transaction() as conn:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        if not knowledge_job_claim_is_current(job_id, claim_token):
            return False, 0
        affected = 0
        if document is None or not document.indexable:
            affected = delete_knowledge_item(source_type, source_id)
        else:
            _persist_document(document)
        complete_knowledge_job(job_id, claim_token=claim_token)
    return True, affected


def process_knowledge_embeddings(limit: int = 25) -> dict[str, Any]:
    """Calcule hors requête un lot durable de vecteurs locaux manquants."""

    import config

    model = str(config.SEMANTIC_SEARCH_MODEL)
    rows = get_missing_knowledge_embeddings(model=model, limit=limit)
    report: dict[str, Any] = {
        "status": "ok",
        "selected": len(rows),
        "indexed": 0,
        "failed": 0,
        "model": model,
    }
    if not rows:
        return report
    try:
        from scripts.semantic_search import (
            SemanticSearchUnavailable,
            embed_text,
            embedding_to_blob,
        )
    except Exception as exc:
        report["status"] = "unavailable"
        report["failed"] = len(rows)
        report["error_code"] = type(exc).__name__
        return report

    for row in rows:
        text = "\n".join(
            part
            for part in (
                str(row.get("title") or "").strip(),
                str(row.get("searchable_text") or "").strip(),
                str(row.get("summary") or "").strip(),
            )
            if part
        )[:20_000]
        try:
            vector = embed_text(text)
            persisted = upsert_knowledge_embedding(
                str(row["uid"]),
                model=model,
                content_hash=str(row["content_hash"]),
                embedding=embedding_to_blob(vector),
            )
            report["indexed"] += int(persisted)
        except SemanticSearchUnavailable as exc:
            report["status"] = "unavailable"
            report["failed"] += 1
            report["error_code"] = type(exc).__name__
            break
        except Exception as exc:
            report["status"] = "degraded"
            report["failed"] += 1
            report["error_code"] = type(exc).__name__
    return report


def backfill_knowledge(
    source_types: Sequence[str] = (),
    *,
    batch_size: int = 200,
    max_items: int | None = None,
    resume: bool = True,
    registry: AdapterRegistry | None = None,
) -> dict[str, Any]:
    """Backfill idempotent, borne et reprenable par adaptateur."""

    active_registry = registry or get_default_registry()
    selected = tuple(source_types or active_registry.source_types)
    states = {row["source_key"]: row for row in get_knowledge_source_states(selected)}
    limit = max(1, min(1_000, int(batch_size)))
    remaining = None if max_items is None else max(0, int(max_items))
    report: dict[str, Any] = {"status": "ok", "indexed": 0, "sources": {}, "errors": {}}

    for adapter in active_registry.adapters_for(selected):
        if remaining == 0:
            break
        if not getattr(adapter, "indexable", True):
            report["sources"][adapter.key] = 0
            update_knowledge_source_state(
                adapter.key,
                adapter.source_type,
                status="ok",
                cursor=None,
                item_count=0,
                last_indexed_at=sqlite_utc_timestamp(),
                last_backfill_at=sqlite_utc_timestamp(),
                error_code=None,
            )
            continue
        cursor = (
            str(states.get(adapter.key, {}).get("cursor") or "0") if resume else "0"
        )
        source_count = (
            int(states.get(adapter.key, {}).get("item_count") or 0) if resume else 0
        )
        processed_count = 0
        try:
            while True:
                current_limit = limit if remaining is None else min(limit, remaining)
                if current_limit <= 0:
                    break
                documents, next_cursor = adapter.iter_batch(cursor, current_limit)
                if not documents:
                    break
                with db_transaction():
                    for document in documents:
                        _persist_document(document)
                source_count += len(documents)
                processed_count += len(documents)
                report["indexed"] += len(documents)
                if remaining is not None:
                    remaining -= len(documents)
                previous_cursor = cursor
                cursor = next_cursor or cursor
                update_knowledge_source_state(
                    adapter.key,
                    adapter.source_type,
                    status="ok",
                    cursor=cursor,
                    item_count=source_count,
                    last_indexed_at=sqlite_utc_timestamp(),
                    last_backfill_at=sqlite_utc_timestamp(),
                    error_code=None,
                )
                if (
                    next_cursor is None
                    or cursor == previous_cursor
                    or len(documents) < current_limit
                ):
                    break
            report["sources"][adapter.key] = processed_count
        except Exception as exc:
            report["status"] = "degraded"
            report["errors"][adapter.key] = type(exc).__name__
            update_knowledge_source_state(
                adapter.key,
                adapter.source_type,
                status="unavailable",
                cursor=cursor,
                item_count=source_count,
                error_code=type(exc).__name__,
            )
    return report


def rebuild_knowledge_index(
    source_types: Sequence[str] = (),
    *,
    batch_size: int = 200,
    max_items: int | None = None,
    registry: AdapterRegistry | None = None,
) -> dict[str, Any]:
    """Reconstruit les projections selectionnees depuis les tables canoniques."""

    if max_items is not None:
        raise ValueError("rebuild_max_items_unsafe")
    active_registry = registry or get_default_registry()
    selected = tuple(source_types or active_registry.source_types)
    deleted = delete_knowledge_sources(selected)
    report = backfill_knowledge(
        selected,
        batch_size=batch_size,
        max_items=max_items,
        resume=False,
        registry=active_registry,
    )
    report["deleted"] = deleted
    return report


def _persist_document(document: KnowledgeDocument) -> str:
    if not document.indexable:
        return document.uid
    return upsert_knowledge_item(
        source_type=document.source_type,
        source_id=document.source_id,
        searchable_text=document.searchable_text,
        title=document.title,
        summary=document.summary,
        chunk_index=document.chunk_index,
        conversation_id=document.conversation_id,
        people=document.people,
        occurred_at=document.occurred_at,
        source_updated_at=document.source_updated_at,
        sensitivity=document.sensitivity,
        cloud_policy=document.cloud_policy,
        trust=document.trust,
        metadata=document.metadata,
    )


def _hit_from_document(
    document: KnowledgeDocument, request: RetrievalRequest
) -> RetrievalHit:
    score, reasons = _score(
        title=document.title,
        text=document.searchable_text,
        summary=document.summary,
        people=document.people,
        request=request,
        conversation_id=document.conversation_id,
    )
    score, reasons = _apply_trust_preference(score, reasons, document.trust)
    return RetrievalHit(
        uid=document.uid,
        source_type=document.source_type,
        source_id=document.source_id,
        title=document.title,
        excerpt=_excerpt(
            document.searchable_text or document.summary, request.query, 1_000
        ),
        content=document.searchable_text,
        conversation_id=document.conversation_id,
        occurred_at=document.occurred_at,
        source_updated_at=document.source_updated_at,
        indexed_at=None,
        score=score,
        reasons=reasons,
        trust=document.trust,
        sensitivity=document.sensitivity,
        cloud_policy=document.cloud_policy,
        metadata=dict(document.metadata),
    )


def _hit_from_index_row(row: dict[str, Any], request: RetrievalRequest) -> RetrievalHit:
    people = tuple(str(value) for value in row.get("people") or ())
    score, reasons = _score(
        title=str(row.get("title") or ""),
        text=str(row.get("searchable_text") or ""),
        summary=str(row.get("summary") or ""),
        people=people,
        request=request,
        conversation_id=_optional_int(row.get("conversation_id")),
    )
    if row.get("fts_rank") is not None:
        score += 1.0
        reasons = tuple(dict.fromkeys((*reasons, "fts")))
    score, reasons = _apply_trust_preference(
        score,
        reasons,
        str(row.get("trust") or "untrusted_stored_data"),
    )
    return RetrievalHit(
        uid=str(row["uid"]),
        source_type=str(row["source_type"]),
        source_id=str(row["source_id"]),
        title=str(row.get("title") or ""),
        excerpt=_excerpt(str(row.get("searchable_text") or ""), request.query, 1_000),
        content=str(row.get("searchable_text") or ""),
        conversation_id=_optional_int(row.get("conversation_id")),
        occurred_at=_optional_text(row.get("occurred_at")),
        source_updated_at=_optional_text(row.get("source_updated_at")),
        indexed_at=_optional_text(row.get("indexed_at")),
        score=round(score, 4),
        reasons=reasons,
        trust=str(row.get("trust") or "untrusted_stored_data"),
        sensitivity=str(row.get("sensitivity") or "personal"),
        cloud_policy=str(row.get("cloud_policy") or "redact"),
        metadata=dict(row.get("metadata") or {}),
    )


def _score(
    *,
    title: str,
    text: str,
    summary: str,
    people: Sequence[str],
    request: RetrievalRequest,
    conversation_id: int | None,
) -> tuple[float, tuple[str, ...]]:
    normalized_title = _fold(title)
    normalized_text = _fold(text)
    normalized_summary = _fold(summary)
    normalized_people = _fold(" ".join(people))
    title_terms = set(_WORD_RE.findall(normalized_title))
    text_terms = set(_WORD_RE.findall(normalized_text))
    summary_terms = set(_WORD_RE.findall(normalized_summary))
    people_terms = set(_WORD_RE.findall(normalized_people))
    phrase = _fold(request.query)
    terms = [
        term
        for term in dict.fromkeys(_WORD_RE.findall(_fold(request.effective_query)))
        if len(term) > 1 and term not in _SCORING_STOPWORDS
    ][:24]
    score = 0.0
    reasons: list[str] = []

    if phrase and phrase in normalized_title:
        score += 12.0
        reasons.append("exact_title")
    elif phrase and phrase in normalized_text:
        score += 8.0
        reasons.append("exact_content")
    for term in terms:
        if term in title_terms:
            score += 4.0
            reasons.append("title_term")
        elif term in summary_terms:
            score += 2.5
            reasons.append("summary_term")
        elif term in text_terms:
            score += 2.0
            reasons.append("content_term")
        if term in people_terms:
            score += 1.5
            reasons.append("person_term")
    if request.person:
        person = _fold(request.person)
        if (
            person
            and person in f"{normalized_people} {normalized_title} {normalized_text}"
        ):
            score += 8.0
            reasons.append("person_filter")
    if (
        request.conversation_id is not None
        and conversation_id == request.conversation_id
    ):
        score += 3.0
        reasons.append("same_conversation")
    if not terms and not reasons:
        score = 1.0
        reasons.append("recent")
    return round(score, 4), tuple(dict.fromkeys(reasons))


def _apply_trust_preference(
    score: float,
    reasons: Sequence[str],
    trust: str,
) -> tuple[float, tuple[str, ...]]:
    if trust == "derived_insight":
        return max(0.0, round(score - 0.5, 4)), tuple(
            dict.fromkeys((*reasons, "derived"))
        )
    return round(score + 0.25, 4), tuple(dict.fromkeys((*reasons, "canonical")))


def _requested_source_types(
    request: RetrievalRequest,
    registry: AdapterRegistry,
) -> tuple[str, ...]:
    """Restreint seulement les intentions de source explicites et non ambiguës."""

    if request.source_types:
        return tuple(request.source_types)
    available = set(registry.source_types)
    selected: list[str] = []
    for pattern, source_types in _SOURCE_INTENTS:
        if not pattern.search(request.effective_query):
            continue
        selected.extend(source for source in source_types if source in available)
    if selected:
        return tuple(dict.fromkeys(selected))
    return tuple(registry.source_types)


def _knowledge_semantic_rows(
    request: RetrievalRequest,
    requested_sources: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, float], str | None]:
    """Rerank local best-effort des projections vectorisées courantes."""

    import config

    model = str(config.SEMANTIC_SEARCH_MODEL)
    try:
        embeddings = get_knowledge_embeddings(
            requested_sources,
            model=model,
            person=request.person,
            from_iso=request.from_iso,
            to_iso=request.to_iso,
            limit=5_000,
        )
    except Exception as exc:
        return [], {}, f"semantic_knowledge:{type(exc).__name__}"
    if not embeddings:
        return [], {}, None
    try:
        from scripts.semantic_search import (
            blob_to_embedding,
            cosine_similarity,
            embed_text,
        )

        query_vector = embed_text(request.effective_query)
        scored: list[tuple[float, str]] = []
        for row in embeddings:
            similarity = float(
                cosine_similarity(
                    query_vector,
                    blob_to_embedding(row["embedding"]),
                )
            )
            if similarity >= 0.35:
                scored.append((similarity, str(row["uid"])))
        scored.sort(key=lambda item: item[0], reverse=True)
        selected = scored[: request.max_candidates]
        rows = [
            row
            for _score_value, uid in selected
            if (row := get_knowledge_item_row(uid)) is not None
        ]
        return (
            rows,
            {uid: score_value for score_value, uid in selected},
            "semantic_backend:knowledge_local" if selected else None,
        )
    except Exception as exc:
        return [], {}, f"semantic_knowledge:{type(exc).__name__}"


def _structured_values_match(
    request: RetrievalRequest,
    *,
    people: Sequence[str],
    title: str,
    searchable_text: str,
    summary: str,
    occurred_at: str | None,
    source_updated_at: str | None,
) -> bool:
    if request.person:
        needle = request.person.casefold()
        searchable = " ".join((*people, title, searchable_text, summary)).casefold()
        if needle not in searchable:
            return False

    if not request.from_iso and not request.to_iso:
        return True
    effective_date = occurred_at or source_updated_at
    if not effective_date:
        return False
    try:
        occurred = datetime.fromisoformat(str(effective_date).replace("Z", "+00:00"))
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=timezone.utc)
        occurred = occurred.astimezone(timezone.utc)
        if request.from_iso:
            lower = datetime.fromisoformat(request.from_iso.replace("Z", "+00:00"))
            if lower.tzinfo is None:
                lower = lower.replace(tzinfo=timezone.utc)
            if occurred < lower.astimezone(timezone.utc):
                return False
        if request.to_iso:
            upper = datetime.fromisoformat(request.to_iso.replace("Z", "+00:00"))
            if upper.tzinfo is None:
                upper = upper.replace(tzinfo=timezone.utc)
            if occurred > upper.astimezone(timezone.utc):
                return False
    except (TypeError, ValueError):
        return False
    return True


def _document_matches_structured_filters(
    document: KnowledgeDocument,
    request: RetrievalRequest,
) -> bool:
    """Applique aux anciens embeddings les mêmes bornes que la recherche canonique."""

    return _structured_values_match(
        request,
        people=document.people,
        title=document.title,
        searchable_text=document.searchable_text,
        summary=document.summary,
        occurred_at=document.occurred_at,
        source_updated_at=document.source_updated_at,
    )


def _index_row_matches_structured_filters(
    row: dict[str, Any],
    request: RetrievalRequest,
) -> bool:
    return _structured_values_match(
        request,
        people=tuple(str(value) for value in row.get("people") or ()),
        title=str(row.get("title") or ""),
        searchable_text=str(row.get("searchable_text") or ""),
        summary=str(row.get("summary") or ""),
        occurred_at=_optional_text(row.get("occurred_at")),
        source_updated_at=(
            _optional_text(row.get("source_updated_at"))
            or _optional_text(row.get("indexed_at"))
        ),
    )


def _legacy_semantic_documents(
    request: RetrievalRequest,
    requested_sources: Sequence[str],
    registry: AdapterRegistry,
) -> tuple[dict[str, KnowledgeDocument], dict[str, float], str | None]:
    """Réutilise les embeddings locaux déjà produits pour épisodes/enregistrements."""

    wanted = set(requested_sources)
    legacy_sources: list[str] = []
    if wanted & {"episode", "note"}:
        legacy_sources.append("episode")
    if "recording" in wanted:
        legacy_sources.append("recording")
    if not legacy_sources or not request.query.strip():
        return {}, {}, None

    try:
        from database import get_all_memory_embeddings

        rows: list[dict[str, Any]] = []
        for source_type in legacy_sources:
            rows.extend(get_all_memory_embeddings(source_type=source_type, limit=5_000))
        if not rows:
            return {}, {}, None

        from scripts.semantic_search import (
            blob_to_embedding,
            cosine_similarity,
            embed_text,
        )

        query_vector = embed_text(request.query)
        scored = sorted(
            (
                (
                    float(
                        cosine_similarity(
                            query_vector, blob_to_embedding(row["embedding"])
                        )
                    ),
                    row,
                )
                for row in rows
            ),
            key=lambda item: item[0],
            reverse=True,
        )[: request.max_candidates]
    except Exception as exc:
        logger.debug(
            "[retrieval] rappel sémantique optionnel indisponible", exc_info=True
        )
        return {}, {}, f"semantic_optional:{type(exc).__name__}"

    documents: dict[str, KnowledgeDocument] = {}
    similarities: dict[str, float] = {}
    for similarity, row in scored:
        if similarity < 0.35:
            continue
        legacy_source = str(row.get("source_type") or "")
        source_id = str(row.get("source_id") or "")
        if legacy_source == "episode":
            targets = tuple(
                source for source in ("note", "episode") if source in wanted
            )
        else:
            targets = ("recording",) if "recording" in wanted else ()
        for target in targets:
            document = None
            for adapter in registry.adapters_for_source(target):
                document = adapter.get(source_id)
                if document is not None:
                    break
            if document is None or not _document_matches_structured_filters(
                document, request
            ):
                continue
            documents[document.uid] = document
            similarities[document.uid] = max(
                similarities.get(document.uid, 0.0),
                similarity,
            )
            # Une note utilisateur ne doit pas aussi ressortir sous l'alias
            # générique épisode lors d'une recherche multi-source.
            break
    return documents, similarities, "semantic_backend:legacy_local"


def _with_implicit_time_range(
    request: RetrievalRequest,
) -> tuple[RetrievalRequest, str | None]:
    """Résout les périodes relatives dans le fuseau configuré (Europe/Paris)."""

    if request.from_iso or request.to_iso:
        return request, None
    query = _fold(request.effective_query)
    today = local_datetime().date()
    start_day = None
    end_day = None
    label = None

    if re.search(r"\bdepuis\s+hier\b", query):
        start_day, end_day, label = (
            today - timedelta(days=1),
            today + timedelta(days=1),
            "depuis_hier",
        )
    elif re.search(r"\bavant[- ]hier\b", query):
        start_day = today - timedelta(days=2)
        label = "avant_hier"
    elif re.search(r"\bhier\b", query):
        start_day = today - timedelta(days=1)
        label = "hier"
    elif re.search(r"\baujourd(?:'|\s)?hui\b", query):
        start_day = today
        label = "aujourd_hui"
    elif re.search(r"\bdemain\b", query):
        start_day = today + timedelta(days=1)
        label = "demain"
    elif re.search(r"\bsemaine\s+(?:derniere|passee)\b", query):
        this_monday = today - timedelta(days=today.weekday())
        start_day, end_day, label = (
            this_monday - timedelta(days=7),
            this_monday,
            "semaine_derniere",
        )
    elif re.search(r"\bsemaine\s+prochaine\b", query):
        this_monday = today - timedelta(days=today.weekday())
        start_day, end_day, label = (
            this_monday + timedelta(days=7),
            this_monday + timedelta(days=14),
            "semaine_prochaine",
        )
    elif re.search(r"\bcette\s+semaine\b", query):
        start_day = today - timedelta(days=today.weekday())
        end_day = start_day + timedelta(days=7)
        label = "cette_semaine"
    elif re.search(r"\b(?:ces\s+)?derniers\s+jours\b", query):
        start_day, end_day, label = (
            today - timedelta(days=6),
            today + timedelta(days=1),
            "sept_jours",
        )

    if start_day is None:
        return request, None
    if end_day is None:
        start, end = utc_bounds_for_local_day(start_day)
    else:
        start, end = utc_bounds_for_local_dates(start_day, end_day)
    inclusive_end = (
        datetime.strptime(end, SQLITE_UTC_FORMAT) - timedelta(seconds=1)
    ).strftime(SQLITE_UTC_FORMAT)
    return replace(request, from_iso=start, to_iso=inclusive_end), label


def _requested_hit_count(request: RetrievalRequest) -> int | None:
    query = _fold(request.query)
    number_match = re.search(
        r"\b([1-8])\s+(?:tout\s+)?(?:dernier|derniers|derniere|dernieres)\b",
        query,
    )
    if number_match:
        return int(number_match.group(1))
    words = {
        "un": 1,
        "une": 1,
        "deux": 2,
        "trois": 3,
        "quatre": 4,
        "cinq": 5,
        "six": 6,
        "sept": 7,
        "huit": 8,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "seven": 7,
        "eight": 8,
    }
    word_match = re.search(
        r"\b(" + "|".join(words) + r")\s+(?:dernier|derniers|derniere|dernieres)\b",
        query,
    )
    if word_match:
        return words[word_match.group(1)]
    if re.search(r"\b(?:mon|le|ce)\s+dernier\b", query):
        return 1
    if _MAIL_SOURCE_RE.search(request.effective_query) and _SUMMARY_RE.search(query):
        return 5
    return None


def _diversify_hits(
    hits: Sequence[RetrievalHit],
    max_hits: int,
) -> list[RetrievalHit]:
    """Réserve une première preuve par source avant de compléter au score."""

    primary: list[RetrievalHit] = []
    remainder: list[RetrievalHit] = []
    seen_sources: set[str] = set()
    target = max(1, min(8, int(max_hits)))
    for hit in hits:
        if hit.source_type not in seen_sources and len(primary) < target:
            primary.append(hit)
            seen_sources.add(hit.source_type)
        else:
            remainder.append(hit)
    return primary + remainder


def _deduplicate_canonical_hits(
    hits: Sequence[RetrievalHit],
) -> list[RetrievalHit]:
    """Évite qu'une même ligne métier consomme plusieurs preuves via ses alias."""

    adapter_groups = {
        "user_notes": "episodes",
        "school_documents_fine": "school_documents",
        "conversation_documents_fine": "conversation_documents",
    }
    fine_priority = {
        "note": 2,
        "journal": 2,
        "school_document": 2,
        "conversation_document": 2,
        "episode": 1,
        "document": 1,
    }
    selected: dict[tuple[str, str], RetrievalHit] = {}
    for hit in hits:
        adapter = str(hit.metadata.get("adapter") or hit.source_type)
        canonical_adapter = adapter_groups.get(adapter, adapter)
        canonical_source_id = hit.source_id
        if canonical_adapter == "school_documents" and canonical_source_id.startswith(
            "school:"
        ):
            canonical_source_id = canonical_source_id.removeprefix("school:")
        elif (
            canonical_adapter == "conversation_documents"
            and canonical_source_id.startswith("conversation:")
        ):
            canonical_source_id = canonical_source_id.removeprefix("conversation:")
        key = (canonical_adapter, canonical_source_id)
        previous = selected.get(key)
        if previous is None:
            selected[key] = hit
            continue
        candidate_rank = (fine_priority.get(hit.source_type, 1), hit.score)
        previous_rank = (fine_priority.get(previous.source_type, 1), previous.score)
        if candidate_rank > previous_rank:
            selected[key] = hit
    return list(selected.values())


def _apply_result_budget(
    hits: Sequence[RetrievalHit], max_hits: int, char_budget: int
) -> list[RetrievalHit]:
    selected: list[RetrievalHit] = []
    remaining = max(256, min(8_000, int(char_budget)))
    for hit in hits[: max(1, min(8, int(max_hits)))]:
        overhead = len(hit.title) + len(hit.uid) + 120
        allowed = min(1_000, max(0, remaining - overhead))
        if allowed < 80:
            break
        excerpt = hit.excerpt[:allowed]
        selected.append(replace(hit, excerpt=excerpt, content=None))
        remaining -= overhead + len(excerpt)
    return selected


def _excerpt(text: str, query: str, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    folded = _fold(compact)
    terms = [term for term in _WORD_RE.findall(_fold(query)) if len(term) > 2]
    positions = [folded.find(term) for term in terms if folded.find(term) >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - limit // 3)
    end = min(len(compact), start + limit)
    prefix = "…" if start else ""
    suffix = "…" if end < len(compact) else ""
    return prefix + compact[start:end] + suffix


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(
        char for char in decomposed if not unicodedata.combining(char)
    ).casefold()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _index_lag_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return round(max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds()), 3)
    except (TypeError, ValueError):
        return None


def retrieval_debug_json(result: RetrievalResult) -> str:
    """Diagnostic borne sans corps integral, utile aux tests et metriques."""

    payload = result.as_dict()
    for hit in payload["hits"]:
        hit.pop("content", None)
        hit["excerpt"] = str(hit.get("excerpt") or "")[:160]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)[:8_000]

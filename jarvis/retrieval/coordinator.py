"""Coordination synchrone et bornee du retrieval multi-source."""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

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

from .models import RetrievalHit, RetrievalRequest, RetrievalResult, SourceCoverage
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
_BROAD_TIMELINE_RE = re.compile(
    r"\b(?:s['’]?est[- ]il\s+pass[eé]|(?:s|c)['’]?est\s+pass[eé]|"
    r"quoi\s+de\s+neuf|actualit[eé]s?|nouvelles?)\b",
    re.IGNORECASE,
)
_LIVE_SOURCE_TYPES = frozenset({"email", "calendar", "imessage"})
_LIVE_COVERAGE_KEYS: Mapping[str, frozenset[str]] = {
    "email": frozenset({"mail", "email_live", "mail_live"}),
    "calendar": frozenset({"calendar", "calendar_live"}),
    "imessage": frozenset({"imessage", "imessage_live"}),
}
_INGESTION_SOURCE_BY_RETRIEVAL: Mapping[str, str] = {
    "email": "mail",
    "calendar": "calendar",
    "imessage": "imessage",
}
_FRENCH_MONTHS: Mapping[str, int] = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}
_FRENCH_WEEKDAYS: Mapping[str, int] = {
    "lundi": 0,
    "mardi": 1,
    "mercredi": 2,
    "jeudi": 3,
    "vendredi": 4,
    "samedi": 5,
    "dimanche": 6,
}
_PERSON_CANDIDATE_STOPWORDS = frozenset(
    {
        "aujourd'hui",
        "demain",
        "hier",
        "dernier",
        "derniere",
        "prochain",
        "prochaine",
        "mail",
        "message",
        "sms",
        "imessage",
        "note",
        "agenda",
        "qui",
        "que",
        "quoi",
        "quand",
        "a",
        "avait",
        "est",
        "m'a",
        "ma",
        "mon",
        "mes",
        "ne",
        "n",
    }
)
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


def _extract_structured_person(query: str) -> str | None:
    """Extrait prudemment une personne explicite sans transformer tout nom commun."""

    name = r"[^\W\d_][\wÀ-ÖØ-öø-ÿ'’\-]*(?:\s+[^\W\d_][\wÀ-ÖØ-öø-ÿ'’\-]*){0,2}"
    titled_name = (
        r"[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’\-]*"
        r"(?:\s+[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’\-]*){0,2}"
    )
    patterns = (
        (rf"\b(?i:de|d['’]|par|avec|concernant)\s+({titled_name})", 0),
        (
            rf"^\s*(?:(?:qu['’]?est-ce\s+que|est-ce\s+que|que|et)\s+)?"
            rf"({name})\s+(?:(?:ne|n['’])\s+)?"
            r"(?:m['’]?as?|m['’]?avait|a|avait|vient)\b",
            re.IGNORECASE,
        ),
    )
    for pattern, flags in patterns:
        match = re.search(pattern, str(query or ""), flags=flags)
        if not match:
            continue
        words: list[str] = []
        for word in match.group(1).strip().split():
            normalized = _fold(word).strip("'’-")
            if normalized in _PERSON_CANDIDATE_STOPWORDS:
                break
            words.append(word.strip(" ,.;:?!"))
        candidate = " ".join(word for word in words if word)[:240]
        if candidate and _fold(candidate) not in _PERSON_CANDIDATE_STOPWORDS:
            return candidate
    return None


def _with_structured_constraints(
    request: RetrievalRequest,
) -> tuple[RetrievalRequest, tuple[str, ...]]:
    diagnostics: list[str] = []
    person = request.person or _extract_structured_person(request.query)
    if person and request.person is None:
        diagnostics.append("structured_person")

    latest_n = request.latest_n
    if latest_n is None:
        latest_n = _requested_hit_count(request)
        if latest_n is not None:
            diagnostics.append(f"structured_latest_n:{latest_n}")

    enriched = replace(request, person=person, latest_n=latest_n)
    enriched, implicit_period = _with_implicit_time_range(enriched)
    if implicit_period:
        diagnostics.append(f"implicit_time_range:{implicit_period}")
    return enriched, tuple(diagnostics)


def _is_chronological_request(request: RetrievalRequest) -> bool:
    return request.latest_n is not None or bool(_CHRONOLOGICAL_RE.search(request.query))


def prepare_retrieval_request(request: RetrievalRequest) -> RetrievalRequest:
    """Expose l'interprétation déterministe aux refreshers live du même tour."""

    if not isinstance(request, RetrievalRequest):
        raise TypeError("prepare_retrieval_request attend RetrievalRequest")
    return _with_structured_constraints(request)[0]


def expected_live_sources(request: RetrievalRequest) -> tuple[str, ...]:
    """Sources live explicitement ou temporellement attendues par la demande."""

    return _expected_live_sources(prepare_retrieval_request(request))


def search_knowledge(request: RetrievalRequest) -> RetrievalResult:
    """Recherche l'index et les sources canoniques sans jamais tout injecter."""

    started_at = time.perf_counter()
    if not isinstance(request, RetrievalRequest):
        raise TypeError("search_knowledge attend RetrievalRequest")
    request, structured_diagnostics = _with_structured_constraints(request)
    registry = get_default_registry()
    requested_sources = _requested_source_types(request, registry)
    diagnostics: list[str] = list(structured_diagnostics)
    ingestion_records = _request_ingestion_freshness(
        request, requested_sources, diagnostics
    )
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
    direct_rows_seen = 0
    now = sqlite_utc_timestamp()

    for adapter in registry.adapters_for(requested_sources):
        try:
            found = adapter.search(request, per_adapter)
            source_successes[adapter.source_type] = (
                source_successes.get(adapter.source_type, 0) + 1
            )
            direct_rows_seen += len(found)
            for document in found:
                documents[document.uid] = document
            if len(documents) > request.max_candidates:
                documents = _trim_documents(documents, request, requested_sources)
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
    if not _is_chronological_request(request):
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

    if len(documents) < request.max_hits and not _is_chronological_request(request):
        semantic_documents, semantic_scores, semantic_diagnostic = (
            _legacy_semantic_documents(request, requested_sources, registry)
        )
        documents.update(semantic_documents)
        if len(documents) > request.max_candidates:
            documents = _trim_documents(documents, request, requested_sources)
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

    deduplicated = _deduplicate_canonical_hits(candidates.values())
    candidates_seen = len(deduplicated)
    ranked = _rank_candidates_bounded(deduplicated, request, requested_sources)
    candidates = {hit.uid: hit for hit in ranked}
    if direct_rows_seen > request.max_candidates or candidates_seen > len(ranked):
        diagnostics.append(
            f"candidate_cap:{direct_rows_seen + len(indexed_rows)}:{len(ranked)}"
        )

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

    try:
        source_states = get_knowledge_source_states(requested_sources)
    except Exception as exc:
        source_states = []
        diagnostics.append(f"source_coverage:{type(exc).__name__}")
    hit_sources = {hit.source_type for hit in final_hits}
    expected_live_sources = set(_expected_live_sources(request))
    coverages: list[SourceCoverage] = []
    partial: set[str] = set()
    for source in requested_sources:
        if (
            source in _LIVE_SOURCE_TYPES
            and source not in expected_live_sources
            and source not in hit_sources
        ):
            continue
        successes = source_successes.get(source, 0)
        failures = source_failures.get(source, 0)
        coverage = _source_coverage(
            source,
            request,
            source_states,
            successes=successes,
            failures=failures,
            has_hits=source in hit_sources,
            ingestion_record=ingestion_records.get(source),
        )
        coverages.append(coverage)
        if coverage.status == "unavailable":
            unavailable.add(source)
        elif coverage.status in {"partial", "unknown"}:
            partial.add(source)
        if successes and coverage.status == "complete":
            verified.add(source)

    try:
        index_freshness_at = latest_knowledge_indexed_at(requested_sources)
    except Exception as exc:
        index_freshness_at = None
        diagnostics.append(f"index_freshness:{type(exc).__name__}")

    if not verified and not final_hits and unavailable and not partial:
        status = "unavailable"
    elif (
        unavailable
        or partial
        or any(
            item.startswith(
                (
                    "index_jobs",
                    "knowledge_index",
                    "persist:",
                    "source_state:",
                    "index_freshness:",
                    "conversation_reference_load:",
                    "conversation_reference_save:",
                    "source_coverage:",
                )
            )
            for item in diagnostics
        )
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
        partial_sources=tuple(sorted(partial)),
        unavailable_sources=tuple(sorted(unavailable)),
        source_coverage=tuple(coverages),
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


def _coverage_payload(state: Mapping[str, Any]) -> Mapping[str, Any]:
    cursor = state.get("cursor")
    if not cursor or not isinstance(cursor, str) or not cursor.lstrip().startswith("{"):
        return {}
    try:
        payload = json.loads(cursor)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _coverage_bounds_include(
    request: RetrievalRequest,
    covered_from: str | None,
    covered_to: str | None,
    *,
    full_history: bool,
) -> bool:
    if full_history:
        return True
    if request.from_iso is None and request.to_iso is None:
        return False
    if request.from_iso is not None and not covered_from:
        return False
    if request.to_iso is not None and not covered_to:
        return False
    try:
        requested_from = (
            datetime.fromisoformat(request.from_iso.replace("Z", "+00:00"))
            if request.from_iso
            else None
        )
        requested_to = (
            datetime.fromisoformat(request.to_iso.replace("Z", "+00:00"))
            if request.to_iso
            else None
        )
        actual_from = (
            datetime.fromisoformat(str(covered_from).replace("Z", "+00:00"))
            if covered_from
            else None
        )
        actual_to = (
            datetime.fromisoformat(str(covered_to).replace("Z", "+00:00"))
            if covered_to
            else None
        )
    except (TypeError, ValueError):
        return False
    requested_from = (
        requested_from.replace(tzinfo=timezone.utc)
        if requested_from is not None and requested_from.tzinfo is None
        else requested_from
    )
    requested_to = (
        requested_to.replace(tzinfo=timezone.utc)
        if requested_to is not None and requested_to.tzinfo is None
        else requested_to
    )
    actual_from = (
        actual_from.replace(tzinfo=timezone.utc)
        if actual_from is not None and actual_from.tzinfo is None
        else actual_from
    )
    actual_to = (
        actual_to.replace(tzinfo=timezone.utc)
        if actual_to is not None and actual_to.tzinfo is None
        else actual_to
    )
    if requested_from is not None and (
        actual_from is None or actual_from > requested_from
    ):
        return False
    if requested_to is not None and (actual_to is None or actual_to < requested_to):
        return False
    return True


def _request_ingestion_freshness(
    request: RetrievalRequest,
    requested_sources: Sequence[str],
    diagnostics: list[str],
) -> dict[str, dict[str, Any]]:
    """Enfile un refresh durable sans effectuer d'I/O connecteur dans le tour."""

    expected = _expected_live_sources(request)
    relevant = tuple(
        source for source in requested_sources if source in _LIVE_SOURCE_TYPES
    )
    if not relevant:
        return {}
    ingestion_sources = tuple(
        _INGESTION_SOURCE_BY_RETRIEVAL[source] for source in expected
    )
    requested_states: Mapping[str, Any] = {}
    if ingestion_sources:
        try:
            from jarvis.ingestion.service import request_ingestion_freshness
        except ImportError:
            diagnostics.append("ingestion_freshness_api_unavailable")
        else:
            collected_states: dict[str, Any] = {}
            per_source_budget = request.freshness_budget_ms // max(
                1, len(ingestion_sources)
            )
            for ingestion_source in ingestion_sources:
                try:
                    response = request_ingestion_freshness(
                        (ingestion_source,),
                        from_iso=request.from_iso,
                        to_iso=request.to_iso,
                        budget_ms=per_source_budget,
                    )
                    collected_states.update(response)
                    diagnostics.append(
                        f"ingestion_freshness_requested:{ingestion_source}"
                    )
                except Exception as exc:
                    diagnostics.append(
                        f"ingestion_freshness:{ingestion_source}:{type(exc).__name__}"
                    )
            requested_states = collected_states

    records: dict[str, dict[str, Any]] = {}
    try:
        from database.ingestion import (
            get_connector_binding,
            get_ingestion_source_state,
        )
    except ImportError:
        return records
    for retrieval_source, ingestion_source in _INGESTION_SOURCE_BY_RETRIEVAL.items():
        if retrieval_source not in relevant:
            continue
        try:
            binding = get_connector_binding(ingestion_source)
            state = requested_states.get(ingestion_source)
            if state is None:
                state = get_ingestion_source_state(ingestion_source)
            records[retrieval_source] = {
                "bound": binding is not None,
                "source": ingestion_source,
                "state": state,
            }
        except Exception as exc:
            diagnostics.append(
                f"ingestion_state:{ingestion_source}:{type(exc).__name__}"
            )
    return records


def _state_value(state: Any, name: str, default: Any = None) -> Any:
    if state is None:
        return default
    if isinstance(state, Mapping):
        return state.get(name, default)
    return getattr(state, name, default)


def _source_coverage(
    source_type: str,
    request: RetrievalRequest,
    states: Sequence[Mapping[str, Any]],
    *,
    successes: int,
    failures: int,
    has_hits: bool,
    ingestion_record: Mapping[str, Any] | None = None,
) -> SourceCoverage:
    if source_type not in _LIVE_SOURCE_TYPES:
        if not successes:
            return SourceCoverage(
                source_type=source_type,
                status="unavailable",
                reason="adapter_unavailable",
            )
        return SourceCoverage(
            source_type=source_type,
            status="partial" if failures else "complete",
            reason="adapter_partial" if failures else "canonical_table_queried",
        )

    if ingestion_record is not None:
        ingestion_source = str(ingestion_record.get("source") or source_type)
        bound = bool(ingestion_record.get("bound"))
        state = ingestion_record.get("state")
        state_status = str(_state_value(state, "status", "idle") or "idle")
        completeness = str(_state_value(state, "completeness", "unknown") or "unknown")
        covered_from = _optional_text(_state_value(state, "coverage_start_utc"))
        covered_to = _optional_text(_state_value(state, "coverage_end_utc"))
        refreshed_at = _optional_text(_state_value(state, "last_success_at"))
        cursor = _state_value(state, "cursor", {})
        item_count = int(
            _state_value(
                state,
                "item_count",
                cursor.get("item_count") if isinstance(cursor, Mapping) else 0,
            )
            or 0
        )
        complete = completeness == "complete" and _coverage_bounds_include(
            request,
            covered_from,
            covered_to,
            full_history=(
                source_type in {"email", "imessage"}
                or (isinstance(cursor, Mapping) and bool(cursor.get("full_history")))
                or (covered_from is None and covered_to is None)
            ),
        )
        if not successes:
            status = "unavailable"
            reason = "adapter_unavailable"
        elif not bound and not has_hits:
            status = "unavailable"
            reason = "connector_unbound"
        elif state_status in {"error", "disabled"} and not has_hits:
            status = "unavailable"
            reason = f"ingestion_{state_status}"
        elif complete and not failures:
            status = "complete"
            reason = "ingestion_coverage_complete"
        elif state is not None or has_hits:
            status = "partial"
            reason = "ingestion_coverage_partial"
        else:
            status = "unknown"
            reason = "ingestion_coverage_missing"
        return SourceCoverage(
            source_type=source_type,
            status=status,
            source_keys=(ingestion_source,),
            covered_from_iso=covered_from,
            covered_to_iso=covered_to,
            refreshed_at=refreshed_at,
            item_count=item_count,
            reason=reason,
        )

    accepted_keys = _LIVE_COVERAGE_KEYS.get(source_type, frozenset())
    relevant = [
        state for state in states if str(state.get("source_key") or "") in accepted_keys
    ]
    source_keys = tuple(
        dict.fromkeys(str(state.get("source_key") or "") for state in relevant)
    )
    refreshed_at = (
        max(
            (
                str(state.get("updated_at") or state.get("last_indexed_at") or "")
                for state in relevant
            ),
            default="",
        )
        or None
    )
    item_count = max(
        (int(state.get("item_count") or 0) for state in relevant),
        default=0,
    )
    covered_from = None
    covered_to = None
    full_history = False
    declared_complete = False
    for state in relevant:
        payload = _coverage_payload(state)
        covered_from = (
            str(
                payload.get("covered_from_iso")
                or payload.get("covered_from")
                or covered_from
                or ""
            )
            or None
        )
        covered_to = (
            str(
                payload.get("covered_to_iso")
                or payload.get("covered_to")
                or covered_to
                or ""
            )
            or None
        )
        full_history = full_history or bool(payload.get("full_history"))
        declared_complete = declared_complete or str(
            payload.get("coverage_status") or payload.get("status") or ""
        ).casefold() in {"complete", "full"}

    complete = declared_complete and _coverage_bounds_include(
        request,
        covered_from,
        covered_to,
        full_history=full_history,
    )
    if not successes:
        status = "unavailable"
        reason = "adapter_unavailable"
    elif complete and not failures:
        status = "complete"
        reason = "ingestion_coverage_complete"
    elif relevant or has_hits:
        status = "partial"
        reason = "ingestion_coverage_partial"
    else:
        status = "unknown"
        reason = "ingestion_coverage_missing"
    return SourceCoverage(
        source_type=source_type,
        status=status,
        source_keys=source_keys,
        covered_from_iso=covered_from,
        covered_to_iso=covered_to,
        refreshed_at=refreshed_at,
        item_count=item_count,
        reason=reason,
    )


def get_knowledge_item(uid: str, max_chars: int = 12_000) -> RetrievalHit | None:
    """Hydrate un UID opaque deja indexe, sans accepter de profil explicite."""

    identifier = str(uid or "").strip()
    if not identifier or len(identifier) > 500:
        return None
    limit = max(1, min(12_000, int(max_chars)))
    row = get_knowledge_item_row(identifier)
    if row is None:
        return None
    source_type = str(row["source_type"])
    source_id = str(row["source_id"])
    canonical: KnowledgeDocument | None = None
    for adapter in get_default_registry().adapters_for_source(source_type):
        canonical = adapter.get(source_id)
        if canonical is not None:
            break
    content = str(
        canonical.searchable_text
        if canonical is not None
        else row.get("searchable_text") or ""
    )[:limit]
    return RetrievalHit(
        uid=str(row["uid"]),
        source_type=source_type,
        source_id=source_id,
        title=str(canonical.title if canonical is not None else row.get("title") or ""),
        excerpt=_excerpt(content, "", min(1_000, limit)),
        content=content,
        conversation_id=_optional_int(
            canonical.conversation_id
            if canonical is not None
            else row.get("conversation_id")
        ),
        occurred_at=_optional_text(
            canonical.occurred_at if canonical is not None else row.get("occurred_at")
        ),
        source_updated_at=_optional_text(
            canonical.source_updated_at
            if canonical is not None
            else row.get("source_updated_at")
        ),
        indexed_at=_optional_text(row.get("indexed_at")),
        score=1.0,
        reasons=("hydrated",),
        trust=str(
            canonical.trust
            if canonical is not None
            else row.get("trust") or "untrusted_stored_data"
        ),
        sensitivity=str(
            canonical.sensitivity
            if canonical is not None
            else row.get("sensitivity") or "personal"
        ),
        cloud_policy=str(
            canonical.cloud_policy
            if canonical is not None
            else row.get("cloud_policy") or "redact"
        ),
        metadata=dict(
            canonical.metadata if canonical is not None else row.get("metadata") or {}
        ),
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


def _absolute_local_day(query: str, today: Any) -> tuple[Any, str] | None:
    iso_match = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", query)
    if iso_match:
        try:
            value = datetime(
                int(iso_match.group(1)),
                int(iso_match.group(2)),
                int(iso_match.group(3)),
            ).date()
            return value, "date_iso"
        except ValueError:
            return None

    months = "|".join(_FRENCH_MONTHS)
    french_match = re.search(
        rf"\b([0-3]?\d)(?:er)?\s+({months})(?:\s+(20\d{{2}}))?\b",
        query,
    )
    if french_match:
        year = int(french_match.group(3) or today.year)
        try:
            value = datetime(
                year,
                _FRENCH_MONTHS[french_match.group(2)],
                int(french_match.group(1)),
            ).date()
            return value, "date_francaise"
        except ValueError:
            return None

    weekdays = "|".join(_FRENCH_WEEKDAYS)
    weekday_match = re.search(
        rf"\b({weekdays})\s+(dernier|prochain)\b",
        query,
    )
    if weekday_match:
        target_weekday = _FRENCH_WEEKDAYS[weekday_match.group(1)]
        if weekday_match.group(2) == "dernier":
            offset = (today.weekday() - target_weekday) % 7 or 7
            return today - timedelta(days=offset), "jour_dernier"
        offset = (target_weekday - today.weekday()) % 7 or 7
        return today + timedelta(days=offset), "jour_prochain"
    return None


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

    absolute_day = _absolute_local_day(query, today)
    if absolute_day is not None:
        start_day, label = absolute_day
    elif re.search(r"\bdepuis\s+hier\b", query):
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
    if request.latest_n is not None:
        return request.latest_n
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


def _hit_timestamp(hit: RetrievalHit) -> float:
    value = hit.occurred_at or hit.source_updated_at or hit.indexed_at
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return 0.0


def _expected_live_sources(request: RetrievalRequest) -> tuple[str, ...]:
    explicit: list[str] = []
    source_query = (
        request.effective_query if request.uses_context_reference else request.query
    )
    for pattern, source_types in _SOURCE_INTENTS:
        if not pattern.search(source_query):
            continue
        explicit.extend(
            source for source in source_types if source in _LIVE_SOURCE_TYPES
        )
    if request.source_types:
        explicit.extend(
            source for source in request.source_types if source in _LIVE_SOURCE_TYPES
        )
    if explicit:
        return tuple(dict.fromkeys(explicit))
    if _BROAD_TIMELINE_RE.search(request.query) or (
        request.from_iso is not None or request.to_iso is not None
    ):
        return ("email", "calendar", "imessage")
    return ()


def _rank_candidates_bounded(
    hits: Sequence[RetrievalHit],
    request: RetrievalRequest,
    requested_sources: Sequence[str],
) -> list[RetrievalHit]:
    """Classe globalement sous quota sans réserver des hits hors sujet."""

    chronological = _is_chronological_request(request)
    if chronological:
        ordered = sorted(
            hits,
            key=lambda hit: (_hit_timestamp(hit), hit.score, hit.uid),
            reverse=True,
        )
    else:
        ordered = sorted(
            hits,
            key=lambda hit: (hit.score, _hit_timestamp(hit), hit.uid),
            reverse=True,
        )

    target = request.max_candidates
    if len(requested_sources) <= 1:
        per_source_cap = target
    elif request.source_types or len(requested_sources) <= 6:
        per_source_cap = max(
            2, (target + len(requested_sources) - 1) // len(requested_sources)
        )
    elif _expected_live_sources(request):
        per_source_cap = 4
    else:
        per_source_cap = 3

    selected: list[RetrievalHit] = []
    selected_uids: set[str] = set()
    counts: dict[str, int] = {}

    # Une timeline large doit conserver au moins une preuve par source live
    # attendue, mais uniquement lorsqu'une preuve a réellement été trouvée.
    for source in _expected_live_sources(request):
        hit = next((item for item in ordered if item.source_type == source), None)
        if hit is None or hit.uid in selected_uids or len(selected) >= target:
            continue
        selected.append(hit)
        selected_uids.add(hit.uid)
        counts[source] = counts.get(source, 0) + 1

    for hit in ordered:
        if hit.uid in selected_uids or len(selected) >= target:
            continue
        if counts.get(hit.source_type, 0) >= per_source_cap:
            continue
        selected.append(hit)
        selected_uids.add(hit.uid)
        counts[hit.source_type] = counts.get(hit.source_type, 0) + 1

    # Les quotas sont un mécanisme de diversité, pas une raison de rendre des
    # slots vides lorsqu'une seule source contient les meilleures preuves.
    for hit in ordered:
        if hit.uid in selected_uids or len(selected) >= target:
            continue
        selected.append(hit)
        selected_uids.add(hit.uid)

    # Pour une timeline large, les réserves doivent aussi survivre au budget
    # final de huit preuves. Sans cela, huit mails mieux scorés peuvent encore
    # évincer Calendar et iMessage après ce classement pourtant diversifié.
    if _expected_live_sources(request):
        return selected[:target]

    # Hors timeline multi-source, l'ordre envoyé au modèle reste celui du
    # ranking, pas celui des quotas intermédiaires.
    selected_set = {hit.uid for hit in selected}
    return [hit for hit in ordered if hit.uid in selected_set][:target]


def _trim_documents(
    documents: Mapping[str, KnowledgeDocument],
    request: RetrievalRequest,
    requested_sources: Sequence[str],
) -> dict[str, KnowledgeDocument]:
    if len(documents) <= request.max_candidates:
        return dict(documents)
    ranked = _rank_candidates_bounded(
        [_hit_from_document(document, request) for document in documents.values()],
        request,
        requested_sources,
    )
    return {hit.uid: documents[hit.uid] for hit in ranked if hit.uid in documents}


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

"""Non-regressions du socle de memoire universelle multi-source."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def knowledge_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import config
    import database

    db_path = tmp_path / "jarvis.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    return db_path


def _seed_every_source() -> None:
    from database import get_db
    from database.knowledge import upsert_calendar_events

    marker = "NebulaMarker"
    with get_db() as conn:
        conversation_id = conn.execute(
            """
            INSERT INTO conversations(title, summary, started_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (f"Conversation {marker}", f"Résumé {marker}"),
        ).lastrowid
        conn.execute(
            "INSERT INTO messages(conversation_id, role, content) VALUES (?, 'user', ?)",
            (conversation_id, f"Message {marker}"),
        )
        conn.execute(
            """
            INSERT INTO email_summaries(
                gmail_id, sender, subject, body, summary, received_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "mail-nebula",
                "Grégoire <gregoire@example.test>",
                f"Email {marker}",
                f"Corps {marker}",
                f"Résumé {marker}",
                "2026-08-15T09:00:00+00:00",
            ),
        )
        handle_id = conn.execute(
            "INSERT INTO imessage_handles(apple_handle_id, handle) VALUES (?, ?)",
            (1, "gregoire@example.test"),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO imessage_messages(
                apple_rowid, guid, handle_id, text, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                1,
                "imessage-nebula",
                handle_id,
                f"iMessage {marker}",
                "2026-08-15T10:00:00Z",
            ),
        )
        conn.execute(
            "INSERT INTO recordings(title, transcription, created_at) VALUES (?, ?, ?)",
            (
                f"Note vocale {marker}",
                f"Transcription {marker}",
                "2026-08-15T11:00:00Z",
            ),
        )
        conn.execute(
            "INSERT INTO episodes(agent, content, summary, created_at) VALUES (?, ?, ?, ?)",
            ("jarvis", f"Episode {marker}", f"Résumé {marker}", "2026-08-15T12:00:00Z"),
        )
        conn.execute(
            "INSERT INTO user_facts(category, content, source) VALUES (?, ?, ?)",
            ("test", f"Fait {marker}", "test"),
        )
        conn.execute(
            "INSERT INTO school_documents(title, content) VALUES (?, ?)",
            (f"Cours {marker}", f"Document scolaire {marker}"),
        )
        conn.execute(
            """
            INSERT INTO conversation_documents(
                conversation_id, filename, original_name, file_path, extracted_text
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                "nebula.txt",
                f"Pièce jointe {marker}",
                "/tmp/nebula.txt",
                f"Document conversation {marker}",
            ),
        )
        conn.execute(
            "INSERT INTO people(name, relationship, personality_notes) VALUES (?, ?, ?)",
            (f"Grégoire {marker}", "collègue", f"Personne {marker}"),
        )
        conn.execute(
            "INSERT INTO tasks(title, description) VALUES (?, ?)",
            (f"Tâche {marker}", f"Description {marker}"),
        )
        conn.execute(
            """
            INSERT INTO control_tasks(
                task_id, profile_id, title, description, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "control-nebula",
                "default",
                f"Contrôle {marker}",
                f"Description {marker}",
                "pending",
                "2026-08-15T13:00:00Z",
                "2026-08-15T13:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO dev_projects(slug, name, isolation_path, project_type)
            VALUES (?, ?, ?, ?)
            """,
            ("nebula", f"Projet {marker}", "/tmp/nebula", f"Type {marker}"),
        )
        conn.execute(
            """
            INSERT INTO agent_runs(
                run_id, profile_id, origin, channel, runtime_id, status, phase,
                category, title, budget_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-nebula",
                "default",
                "test",
                "test",
                "runtime-nebula",
                "running",
                "execute",
                "agentic_readonly",
                f"Run {marker}",
                "{}",
                "2026-08-15T14:00:00Z",
                "2026-08-15T14:00:00Z",
            ),
        )

    assert (
        upsert_calendar_events(
            [
                {
                    "external_id": "calendar-nebula",
                    "calendar_name": "Travail",
                    "title": f"Rendez-vous {marker}",
                    "start_at": "2026-08-15T15:00:00+00:00",
                    "end_at": "2026-08-15T16:00:00+00:00",
                    "location": "Paris",
                    "notes": f"Notes {marker}",
                }
            ]
        )
        == 1
    )


def test_every_canonical_source_is_backfilled_and_searchable(
    knowledge_db: Path,
) -> None:
    from jarvis.retrieval import (
        RetrievalRequest,
        rebuild_knowledge_index,
        search_knowledge,
    )

    _seed_every_source()
    report = rebuild_knowledge_index()

    assert report["status"] == "ok"
    assert report["errors"] == {}
    seeded_adapter_keys = {
        "conversations",
        "conversation_messages",
        "email_cache",
        "calendar_cache",
        "imessage_messages",
        "recordings",
        "episodes",
        "user_facts",
        "school_documents",
        "conversation_documents",
        "people",
        "tasks",
        "control_tasks",
        "dev_projects",
        "agent_runs",
    }
    assert seeded_adapter_keys <= set(report["sources"])

    from jarvis.retrieval.models import CANONICAL_SOURCE_TYPES
    from jarvis.retrieval.registry import get_default_registry

    registry = get_default_registry()
    assert set(registry.source_types) == set(CANONICAL_SOURCE_TYPES)

    indexed_source_types = {
        "conversation",
        "message",
        "email",
        "calendar",
        "imessage",
        "recording",
        "episode",
        "fact",
        "document",
        "person",
        "task",
        "control_task",
        "project",
        "agent_run",
    }
    for source_type in sorted(indexed_source_types):
        result = search_knowledge(
            RetrievalRequest(query="NebulaMarker", source_types=(source_type,))
        )
        assert result.status == "ok", (source_type, result.diagnostics)
        assert result.verified_sources == (source_type,)
        assert result.hits, source_type
        assert all(hit.source_type == source_type for hit in result.hits)
        assert all(hit.content is None for hit in result.hits)
        assert all(hit.uid.count(":") >= 2 for hit in result.hits)

    # Même vides, toutes les sources publiques doivent posséder au moins un
    # adaptateur sain : vide vérifié et source indisponible sont deux états
    # différents dans le contrat.
    for source_type in sorted(CANONICAL_SOURCE_TYPES - indexed_source_types):
        result = search_knowledge(
            RetrievalRequest(query="NebulaMarker", source_types=(source_type,))
        )
        assert result.status == "ok", (source_type, result.diagnostics)
        assert result.verified_sources == (source_type,)
        assert result.unavailable_sources == ()

    from database.knowledge import get_knowledge_source_states

    states = {state["source_key"]: state for state in get_knowledge_source_states()}
    assert all(states[key]["item_count"] > 0 for key in seeded_adapter_keys)


def test_calendar_cache_is_idempotent_and_enqueues_reindex(knowledge_db: Path) -> None:
    from database import get_cached_calendar_events, get_db, upsert_calendar_events
    from jarvis.retrieval import (
        RetrievalRequest,
        process_knowledge_jobs,
        search_knowledge,
    )

    event = {
        "external_id": "calendar-42",
        "calendar_name": "Personnel",
        "title": "Déjeuner avec Grégoire",
        "start_at": "2026-08-16T12:00:00+00:00",
        "end_at": "2026-08-16T13:00:00+00:00",
        "notes": "Parler du projet Atlas",
    }
    assert upsert_calendar_events([event]) == 1
    event["notes"] = "Parler du projet Atlas et du budget"
    assert upsert_calendar_events([event]) == 1

    cached = get_cached_calendar_events(
        from_iso="2026-08-16T00:00:00+00:00",
        to_iso="2026-08-16T23:59:59+00:00",
    )
    assert len(cached) == 1
    assert cached[0]["external_id"] == "calendar-42"
    assert "budget" in cached[0]["notes"]

    report = process_knowledge_jobs()
    assert report["failed"] == 0
    with get_db() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM knowledge_items WHERE source_type = 'calendar'"
            ).fetchone()[0]
            == 1
        )
    result = search_knowledge(
        RetrievalRequest(query="budget", source_types=("calendar",))
    )
    assert [hit.source_type for hit in result.hits] == ["calendar"]


def test_latest_mail_intent_returns_read_and_unread_messages_in_date_order(
    knowledge_db: Path,
) -> None:
    from database import get_db
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO email_summaries(
                gmail_id, sender, subject, body, received_at, is_read
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "older-unread",
                "Autre",
                "Ancien sujet",
                "Ancien corps",
                "2026-08-14 09:00:00",
                0,
            ),
        )
        conn.execute(
            """
            INSERT INTO email_summaries(
                gmail_id, sender, subject, body, received_at, is_read
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "gregoire-read",
                "Grégoire",
                "Validation Atlas",
                "La validation est terminée",
                "2026-08-15 20:15:00",
                1,
            ),
        )

    for query in ("résume mes mails", "je veux que tu lises mes 3 derniers mails"):
        latest = search_knowledge(RetrievalRequest(query=query))
        assert latest.verified_sources == ("email",)
        assert [hit.title for hit in latest.hits] == [
            "Validation Atlas",
            "Ancien sujet",
        ]

    from_gregoire = search_knowledge(
        RetrievalRequest(query="Grégoire ne m'a pas envoyé de mail ?")
    )
    assert [hit.title for hit in from_gregoire.hits] == ["Validation Atlas"]


def test_hydration_formatting_budgets_and_profile_isolation(knowledge_db: Path) -> None:
    from database import init_db, use_profile
    from database.knowledge import upsert_knowledge_item
    from jarvis.retrieval import (
        RetrievalRequest,
        format_retrieval_context,
        get_knowledge_item,
        search_knowledge,
    )

    with use_profile("alpha"):
        init_db()
        uid = upsert_knowledge_item(
            source_type="fact",
            source_id="private-alpha",
            searchable_text="SecretAlpha " + ("x" * 2_000),
            title="Mémoire alpha",
        )
        result = search_knowledge(
            RetrievalRequest(
                query="SecretAlpha",
                source_types=("fact",),
                char_budget=512,
            )
        )
        assert result.hits[0].uid == uid
        assert result.hits[0].content is None
        hydrated = get_knowledge_item(uid, max_chars=300)
        assert hydrated is not None
        assert hydrated.content is not None
        assert len(hydrated.content) == 300
        formatted = format_retrieval_context(result, max_chars=512)
        assert len(formatted) <= 512
        assert formatted.startswith("[UNTRUSTED_DATA:KNOWLEDGE_RETRIEVAL]")
        assert uid in formatted
        assert ("SecretAlpha " + ("x" * 2_000)) not in formatted
        envelope = json.loads(formatted.splitlines()[1])
        bounded_payload = json.loads(envelope["content"])
        assert bounded_payload["status"] == "ok"

    with use_profile("beta"):
        init_db()
        assert get_knowledge_item(uid) is None
        result = search_knowledge(
            RetrievalRequest(query="SecretAlpha", source_types=("fact",))
        )
        assert result.hits == ()


def test_request_contract_clamps_untrusted_limits() -> None:
    from jarvis.retrieval import RetrievalRequest

    request = RetrievalRequest(
        query="  trouve   Grégoire  ",
        recent_user_turns=tuple(str(index) for index in range(10)),
        max_candidates=999,
        max_hits=999,
        char_budget=999_999,
    )
    assert request.query == "trouve Grégoire"
    assert request.recent_user_turns == ("4", "5", "6", "7", "8", "9")
    assert request.max_candidates == 20
    assert request.max_hits == 8
    assert request.char_budget == 8_000
    assert request.effective_query == "trouve Grégoire"

    referential = RetrievalRequest(
        query="Résume celui dont je parlais",
        recent_user_turns=("Le message de Grégoire",),
    )
    assert referential.uses_context_reference is True
    assert "Le message de Grégoire" in referential.effective_query

    with pytest.raises(ValueError, match="unknown_source_type"):
        RetrievalRequest(query="test", source_types=("filesystem",))


def test_conversation_references_recall_the_previous_provenance(
    knowledge_db: Path,
) -> None:
    from database import create_conversation, get_db
    from database.knowledge import (
        delete_knowledge_item,
        get_recent_knowledge_references,
        upsert_knowledge_item,
    )
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    conversation_id = create_conversation("orchestrator")
    other_conversation_id = create_conversation("orchestrator")
    poisoned_conversation_id = create_conversation("orchestrator")
    uid = upsert_knowledge_item(
        source_type="fact",
        source_id="referenced-proof",
        title="Preuve Atlas",
        searchable_text="QuartzReferencePersistante",
    )

    first = search_knowledge(
        RetrievalRequest(
            query="QuartzReferencePersistante",
            conversation_id=conversation_id,
            source_types=("fact",),
        )
    )
    assert [hit.uid for hit in first.hits] == [uid]
    assert [row["uid"] for row in get_recent_knowledge_references(conversation_id)] == [
        uid
    ]

    follow_up = search_knowledge(
        RetrievalRequest(
            query="et celui-là ?",
            conversation_id=conversation_id,
            source_types=("fact",),
        )
    )
    assert [hit.uid for hit in follow_up.hits] == [uid]
    assert "conversation_reference" in follow_up.hits[0].reasons

    isolated = search_knowledge(
        RetrievalRequest(
            query="et celui-là ?",
            conversation_id=other_conversation_id,
            source_types=("fact",),
        )
    )
    assert isolated.hits == ()

    poisoned_uid = upsert_knowledge_item(
        source_type="email",
        source_id="restricted-email",
        searchable_text="QuartzRestrictedEmail",
    )
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_retrieval_references(
                conversation_id, uid, source_type, source_id, rank
            ) VALUES (?, ?, 'fact', 'forged-fact', 0)
            """,
            (poisoned_conversation_id, poisoned_uid),
        )
    scoped = search_knowledge(
        RetrievalRequest(
            query="et celui-là ?",
            conversation_id=poisoned_conversation_id,
            source_types=("fact",),
        )
    )
    assert all(hit.source_type == "fact" for hit in scoped.hits)
    assert poisoned_uid not in {hit.uid for hit in scoped.hits}

    assert delete_knowledge_item("fact", "referenced-proof") == 1
    assert get_recent_knowledge_references(conversation_id) == []


def test_local_only_hit_never_formats_its_excerpt() -> None:
    from jarvis.retrieval import (
        RetrievalHit,
        RetrievalResult,
        format_retrieval_context,
    )

    result = RetrievalResult(
        status="ok",
        query="projet",
        hits=(
            RetrievalHit(
                uid="project:1:0",
                source_type="project",
                source_id="1",
                title="Projet local",
                excerpt="SECRET_LOCAL_NE_DOIT_PAS_SORTIR",
                cloud_policy="local_only",
            ),
        ),
    )
    formatted = format_retrieval_context(result)
    assert formatted.startswith("[UNTRUSTED_DATA:KNOWLEDGE_RETRIEVAL]")
    assert "SECRET_LOCAL_NE_DOIT_PAS_SORTIR" not in formatted
    assert "CONTENU LOCAL UNIQUEMENT" in formatted


def test_formatter_bounds_final_wrapper_and_keeps_inner_json_valid() -> None:
    from jarvis.retrieval import (
        RetrievalHit,
        RetrievalResult,
        format_retrieval_context,
    )

    noisy = ('"chemin\\segment" ' * 1_000).strip()
    result = RetrievalResult(
        status="ok",
        query=noisy,
        hits=tuple(
            RetrievalHit(
                uid=f"fact:{index}:0",
                source_type="fact",
                source_id=str(index),
                title=noisy,
                excerpt=noisy,
            )
            for index in range(8)
        ),
    )

    formatted = format_retrieval_context(result, max_chars=8_000)
    assert len(formatted) <= 8_000
    envelope = json.loads(formatted.splitlines()[1])
    assert json.loads(envelope["content"])["status"] == "ok"


def test_one_broken_source_degrades_without_hiding_healthy_hits(
    knowledge_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.retrieval import RetrievalRequest, search_knowledge
    from jarvis.retrieval.registry import (
        AdapterRegistry,
        KnowledgeDocument,
    )
    from jarvis.retrieval import coordinator

    class HealthyAdapter:
        key = "healthy-facts"
        source_type = "fact"

        def search(self, request, limit):
            return [
                KnowledgeDocument(
                    source_type="fact",
                    source_id="healthy",
                    title="Fait sain",
                    searchable_text="Projet Atlas",
                )
            ]

        def get(self, source_id):
            return None

        def iter_batch(self, cursor, limit):
            return [], None

    class BrokenAdapter:
        key = "broken-email"
        source_type = "email"

        def search(self, request, limit):
            raise RuntimeError("source_offline")

        def get(self, source_id):
            return None

        def iter_batch(self, cursor, limit):
            return [], None

    registry = AdapterRegistry((HealthyAdapter(), BrokenAdapter()))
    monkeypatch.setattr(coordinator, "get_default_registry", lambda: registry)

    result = search_knowledge(
        RetrievalRequest(query="Atlas", source_types=("fact", "email"))
    )
    assert result.status == "degraded"
    assert result.verified_sources == ("fact",)
    assert result.unavailable_sources == ("email",)
    assert [hit.uid for hit in result.hits] == ["fact:healthy:0"]


def test_ranking_is_score_desc_then_date_desc(
    knowledge_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.retrieval import RetrievalRequest, search_knowledge
    from jarvis.retrieval import coordinator
    from jarvis.retrieval.registry import AdapterRegistry, KnowledgeDocument

    class RankingAdapter:
        key = "ranking-facts"
        source_type = "fact"

        def search(self, request, limit):
            return [
                KnowledgeDocument(
                    source_type="fact",
                    source_id="exact-old",
                    title="Atlas",
                    searchable_text="Atlas",
                    occurred_at="2025-01-01T00:00:00Z",
                ),
                KnowledgeDocument(
                    source_type="fact",
                    source_id="exact-new",
                    title="Atlas",
                    searchable_text="Atlas",
                    occurred_at="2026-01-01T00:00:00Z",
                ),
                KnowledgeDocument(
                    source_type="fact",
                    source_id="content-newest",
                    title="Autre",
                    searchable_text="Atlas",
                    occurred_at="2027-01-01T00:00:00Z",
                ),
            ]

        def get(self, source_id):
            return None

        def iter_batch(self, cursor, limit):
            return [], None

    registry = AdapterRegistry((RankingAdapter(),))
    monkeypatch.setattr(coordinator, "get_default_registry", lambda: registry)

    result = search_knowledge(RetrievalRequest(query="Atlas", source_types=("fact",)))
    assert [hit.source_id for hit in result.hits] == [
        "exact-new",
        "exact-old",
        "content-newest",
    ]


def test_legacy_semantic_embedding_recalls_paraphrased_note(
    knowledge_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from database import get_db, upsert_memory_embedding
    from jarvis.retrieval import RetrievalRequest, search_knowledge
    from scripts import semantic_search

    with get_db() as conn:
        note_id = conn.execute(
            """
            INSERT INTO episodes(agent, content, summary, created_at)
            VALUES ('user', ?, ?, ?)
            """,
            (
                "Le train part à sept heures pour Lyon.",
                "Départ ferroviaire",
                "2026-08-10T08:00:00Z",
            ),
        ).lastrowid
    upsert_memory_embedding(
        "episode",
        int(note_id),
        "Le train part à sept heures pour Lyon.",
        b"fake-vector",
        "test-model",
    )
    monkeypatch.setattr(semantic_search, "embed_text", lambda _text: [1.0])
    monkeypatch.setattr(semantic_search, "blob_to_embedding", lambda _blob: [1.0])
    monkeypatch.setattr(
        semantic_search,
        "cosine_similarity",
        lambda _left, _right: 0.92,
    )

    result = search_knowledge(
        RetrievalRequest(
            query="Comment devais-je rejoindre la capitale des Gaules à l’aube ?",
            source_types=("note",),
        )
    )

    assert result.status == "ok"
    assert result.hits[0].source_type == "note"
    assert "train" in result.hits[0].excerpt
    assert "semantic" in result.hits[0].reasons


def test_legacy_semantic_embedding_respects_time_filters(
    knowledge_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from database import get_db, upsert_memory_embedding
    from jarvis.retrieval import RetrievalRequest, search_knowledge
    from scripts import semantic_search

    with get_db() as conn:
        old_id = int(
            conn.execute(
                "INSERT INTO episodes(agent, content, created_at) VALUES ('user', ?, ?)",
                ("Souvenir Grégoire ancien", "2026-08-01T08:00:00Z"),
            ).lastrowid
        )
        current_id = int(
            conn.execute(
                "INSERT INTO episodes(agent, content, created_at) VALUES ('user', ?, ?)",
                ("Souvenir Grégoire actuel", "2026-08-15T08:00:00Z"),
            ).lastrowid
        )
    for source_id in (old_id, current_id):
        upsert_memory_embedding(
            "episode",
            source_id,
            "vecteur sans correspondance lexicale",
            b"fake-vector",
            "test-model",
        )
    monkeypatch.setattr(semantic_search, "embed_text", lambda _text: [1.0])
    monkeypatch.setattr(semantic_search, "blob_to_embedding", lambda _blob: [1.0])
    monkeypatch.setattr(
        semantic_search,
        "cosine_similarity",
        lambda _left, _right: 0.92,
    )

    result = search_knowledge(
        RetrievalRequest(
            query="paraphrase totalement différente",
            source_types=("note",),
            person="Grégoire",
            from_iso="2026-08-15T00:00:00Z",
            to_iso="2026-08-15T23:59:59Z",
        )
    )

    assert [hit.source_id for hit in result.hits] == [str(current_id)]


def test_note_write_update_delete_is_transactionally_reindexed(
    knowledge_db: Path,
) -> None:
    from database import get_db
    from jarvis.retrieval import RetrievalRequest, get_knowledge_item, search_knowledge

    with get_db() as conn:
        note_id = int(
            conn.execute(
                "INSERT INTO episodes(agent, content) VALUES ('user', ?)",
                ("QuartzInitial",),
            ).lastrowid
        )
    with get_db() as conn:
        queued = conn.execute(
            """
            SELECT operation, status FROM knowledge_index_jobs
            WHERE source_type = 'note' AND source_id = ?
            """,
            (str(note_id),),
        ).fetchone()
    assert tuple(queued) == ("upsert", "pending")

    created = search_knowledge(
        RetrievalRequest(query="QuartzInitial", source_types=("note",))
    )
    assert len(created.hits) == 1
    uid = created.hits[0].uid

    with get_db() as conn:
        conn.execute(
            "UPDATE episodes SET content = ? WHERE id = ?",
            ("QuartzUpdated", note_id),
        )
    updated = search_knowledge(
        RetrievalRequest(query="QuartzUpdated", source_types=("note",))
    )
    assert [hit.uid for hit in updated.hits] == [uid]
    assert "QuartzInitial" not in updated.hits[0].excerpt

    with get_db() as conn:
        conn.execute("DELETE FROM episodes WHERE id = ?", (note_id,))
    deleted = search_knowledge(
        RetrievalRequest(query="QuartzUpdated", source_types=("note",))
    )
    assert deleted.status == "ok"
    assert deleted.hits == ()
    assert get_knowledge_item(uid) is None


def test_index_job_reenqueue_during_running_preserves_latest_wakeup(
    knowledge_db: Path,
) -> None:
    from database import get_db
    from database.knowledge import (
        claim_knowledge_jobs,
        complete_knowledge_job,
        enqueue_knowledge_job,
        fail_knowledge_job,
    )

    job_id = enqueue_knowledge_job("note", "42", operation="upsert")
    claimed = claim_knowledge_jobs()
    assert [job["id"] for job in claimed] == [job_id]

    enqueue_knowledge_job("note", "42", operation="delete")
    complete_knowledge_job(job_id, claim_token=claimed[0]["updated_at"])
    fail_knowledge_job(
        job_id,
        "stale_worker",
        claim_token=claimed[0]["updated_at"],
    )

    with get_db() as conn:
        row = conn.execute(
            "SELECT operation, status, attempts FROM knowledge_index_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    assert tuple(row) == ("delete", "pending", 0)


def test_stale_running_index_job_is_reclaimed_after_lease(
    knowledge_db: Path,
) -> None:
    from database import get_db
    from database.knowledge import claim_knowledge_jobs, enqueue_knowledge_job

    job_id = enqueue_knowledge_job("note", "84")
    assert [job["id"] for job in claim_knowledge_jobs(lease_seconds=30)] == [job_id]
    assert claim_knowledge_jobs(lease_seconds=30) == []

    with get_db() as conn:
        conn.execute(
            """
            UPDATE knowledge_index_jobs
            SET claimed_at = '2000-01-01 00:00:00'
            WHERE id = ?
            """,
            (job_id,),
        )
    assert [job["id"] for job in claim_knowledge_jobs(lease_seconds=30)] == [job_id]
    with get_db() as conn:
        row = conn.execute(
            "SELECT status, attempts FROM knowledge_index_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    assert tuple(row) == ("running", 2)


def test_reclaimed_job_fences_obsolete_worker_side_effect(
    knowledge_db: Path,
) -> None:
    from database import get_db
    from database.knowledge import (
        claim_knowledge_jobs,
        complete_knowledge_job,
        enqueue_knowledge_job,
        fail_knowledge_job,
        get_knowledge_item_row,
    )
    from jarvis.retrieval.coordinator import _apply_claimed_projection
    from jarvis.retrieval.registry import KnowledgeDocument

    job_id = enqueue_knowledge_job("fact", "lease-race")
    stale_claim = claim_knowledge_jobs(lease_seconds=30)[0]
    with get_db() as conn:
        conn.execute(
            "UPDATE knowledge_index_jobs SET claimed_at = ? WHERE id = ?",
            ("2000-01-01 00:00:00", job_id),
        )
    current_claim = claim_knowledge_jobs(lease_seconds=30)[0]

    applied, _ = _apply_claimed_projection(
        job_id,
        str(stale_claim["updated_at"]),
        "fact",
        "lease-race",
        KnowledgeDocument(
            source_type="fact",
            source_id="lease-race",
            title="Obsolète",
            searchable_text="Projection obsolète",
        ),
    )
    assert applied is False
    assert get_knowledge_item_row("fact:lease-race:0") is None
    fail_knowledge_job(
        job_id,
        "stale_failure",
        claim_token=str(stale_claim["updated_at"]),
    )
    complete_knowledge_job(job_id, claim_token=str(stale_claim["updated_at"]))
    with get_db() as conn:
        running = conn.execute(
            "SELECT status, updated_at FROM knowledge_index_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    assert tuple(running) == ("running", str(current_claim["updated_at"]))

    applied, _ = _apply_claimed_projection(
        job_id,
        str(current_claim["updated_at"]),
        "fact",
        "lease-race",
        KnowledgeDocument(
            source_type="fact",
            source_id="lease-race",
            title="Actuelle",
            searchable_text="Projection actuelle",
        ),
    )
    assert applied is True
    item = get_knowledge_item_row("fact:lease-race:0")
    assert item is not None
    assert item["searchable_text"] == "Projection actuelle"
    fail_knowledge_job(
        job_id,
        "stale_after_done",
        claim_token=str(stale_claim["updated_at"]),
    )
    complete_knowledge_job(job_id, claim_token=str(stale_claim["updated_at"]))
    with get_db() as conn:
        status = conn.execute(
            "SELECT status FROM knowledge_index_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()["status"]
    assert status == "done"


def test_dirty_claim_never_persists_the_obsolete_projection(
    knowledge_db: Path,
) -> None:
    from database.knowledge import enqueue_knowledge_job, get_knowledge_item_row
    from jarvis.retrieval import process_knowledge_jobs
    from jarvis.retrieval.registry import AdapterRegistry, KnowledgeDocument

    class RacingAdapter:
        key = "racing-fact"
        source_type = "fact"
        indexable = True
        first = True

        def search(self, request, limit):
            return []

        def get(self, source_id):
            if self.first:
                self.first = False
                enqueue_knowledge_job("fact", source_id, operation="upsert")
                return KnowledgeDocument(
                    source_type="fact",
                    source_id=source_id,
                    title="Ancien",
                    searchable_text="Projection obsolète",
                )
            return KnowledgeDocument(
                source_type="fact",
                source_id=source_id,
                title="Nouveau",
                searchable_text="Projection actuelle",
            )

        def iter_batch(self, cursor, limit):
            return [], None

    registry = AdapterRegistry((RacingAdapter(),))
    enqueue_knowledge_job("fact", "racing-source")
    process_knowledge_jobs(limit=1, registry=registry)
    assert get_knowledge_item_row("fact:racing-source:0") is None

    process_knowledge_jobs(limit=1, registry=registry)
    item = get_knowledge_item_row("fact:racing-source:0")
    assert item is not None
    assert item["searchable_text"] == "Projection actuelle"


def test_partial_rebuild_is_rejected_before_deleting_projection(
    knowledge_db: Path,
) -> None:
    from database.knowledge import get_knowledge_item_row, upsert_knowledge_item
    from jarvis.retrieval import rebuild_knowledge_index

    uid = upsert_knowledge_item(
        source_type="fact",
        source_id="survives-unsafe-rebuild",
        searchable_text="Projection à conserver",
    )
    with pytest.raises(ValueError, match="rebuild_max_items_unsafe"):
        rebuild_knowledge_index(max_items=1)
    assert get_knowledge_item_row(uid) is not None


def test_fts_person_filter_uses_qualified_columns(knowledge_db: Path) -> None:
    from database.knowledge import search_knowledge_items, upsert_knowledge_item

    uid = upsert_knowledge_item(
        source_type="email",
        source_id="mail-filtered",
        searchable_text="Décision Atlas validée",
        title="Atlas",
        people=("Grégoire",),
    )
    rows, backend = search_knowledge_items(
        "Atlas",
        source_types=("email",),
        person="Grégoire",
    )
    assert backend == "fts"
    assert [row["uid"] for row in rows] == [uid]


def test_time_filter_prefers_source_update_over_index_time(knowledge_db: Path) -> None:
    from database.knowledge import search_knowledge_items, upsert_knowledge_item

    uid = upsert_knowledge_item(
        source_type="fact",
        source_id="historical-source-update",
        searchable_text="QuartzDateCanonique",
        source_updated_at="2026-08-01T10:00:00Z",
    )
    historical, _ = search_knowledge_items(
        "QuartzDateCanonique",
        source_types=("fact",),
        from_iso="2026-08-01T00:00:00Z",
        to_iso="2026-08-01T23:59:59Z",
    )
    current, _ = search_knowledge_items(
        "QuartzDateCanonique",
        source_types=("fact",),
        from_iso="2026-08-16T00:00:00Z",
        to_iso="2026-08-16T23:59:59Z",
    )
    assert [row["uid"] for row in historical] == [uid]
    assert current == []


def test_observability_failures_degrade_without_dropping_hits(
    knowledge_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from database.knowledge import upsert_knowledge_item
    from jarvis.retrieval import RetrievalRequest, search_knowledge
    from jarvis.retrieval import coordinator

    uid = upsert_knowledge_item(
        source_type="fact",
        source_id="observable-proof",
        searchable_text="QuartzObservabilityProof",
    )
    monkeypatch.setattr(
        coordinator,
        "update_knowledge_source_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("state down")),
    )
    monkeypatch.setattr(
        coordinator,
        "latest_knowledge_indexed_at",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("freshness down")),
    )

    result = search_knowledge(
        RetrievalRequest(
            query="QuartzObservabilityProof",
            source_types=("fact",),
        )
    )

    assert [hit.uid for hit in result.hits] == [uid]
    assert result.status == "degraded"
    assert any(item.startswith("source_state:") for item in result.diagnostics)
    assert any(item.startswith("index_freshness:") for item in result.diagnostics)


def test_observability_report_contains_metrics_but_no_personal_content(
    knowledge_db: Path,
) -> None:
    from database.knowledge import (
        claim_knowledge_jobs,
        enqueue_knowledge_job,
        fail_knowledge_job,
        get_knowledge_observability,
        upsert_knowledge_item,
    )

    upsert_knowledge_item(
        source_type="fact",
        source_id="private-observability-id",
        searchable_text="CanaryPersonalContent",
    )
    job_id = enqueue_knowledge_job("fact", "job-private-id")
    claim_knowledge_jobs()
    fail_knowledge_job(job_id, "private error details")

    report = get_knowledge_observability()
    serialized = json.dumps(report, ensure_ascii=False)
    assert report["coverage_by_source"]["fact"] == 1
    assert report["jobs_by_status"]["retry"] == 1
    assert report["errors_by_code"] == {"redacted_error_code": 1}
    assert "CanaryPersonalContent" not in serialized
    assert "private-observability-id" not in serialized
    assert "job-private-id" not in serialized


def test_generic_retrieval_deduplicates_legacy_and_fine_source_aliases(
    knowledge_db: Path,
) -> None:
    from database import get_db
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    with get_db() as conn:
        conn.execute(
            "INSERT INTO episodes(agent, content) VALUES ('user', ?)",
            ("QuartzAliasUnique",),
        )

    result = search_knowledge(RetrievalRequest(query="rappelle QuartzAliasUnique"))
    matching = [hit for hit in result.hits if "QuartzAliasUnique" in hit.excerpt]
    assert len(matching) == 1
    assert matching[0].source_type == "note"


def test_canonical_dedup_keeps_journal_and_collapses_document_alias(
    knowledge_db: Path,
) -> None:
    from database import get_db
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    with get_db() as conn:
        conn.execute(
            "INSERT INTO episodes(agent, content) VALUES ('user', ?)",
            ("QuartzDistinctCanonical",),
        )
        conn.execute(
            "INSERT INTO jarvis_journal(date, entry) VALUES (?, ?)",
            ("2026-08-15", "QuartzDistinctCanonical"),
        )
        conn.execute(
            "INSERT INTO school_documents(title, content) VALUES (?, ?)",
            ("QuartzDocumentAlias", "Contenu QuartzDocumentAlias"),
        )

    distinct = search_knowledge(
        RetrievalRequest(
            query="QuartzDistinctCanonical",
            source_types=("note", "journal"),
        )
    )
    assert {hit.source_type for hit in distinct.hits} == {"note", "journal"}

    document = search_knowledge(
        RetrievalRequest(
            query="QuartzDocumentAlias",
            source_types=("document", "school_document"),
        )
    )
    matches = [hit for hit in document.hits if "QuartzDocumentAlias" in hit.excerpt]
    assert len(matches) == 1
    assert matches[0].source_type == "school_document"


def test_maintenance_worker_backfills_preexisting_profile_data(
    knowledge_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from database import get_db
    from database.knowledge import search_knowledge_items
    from jarvis.retrieval import worker

    with get_db() as conn:
        conn.execute(
            "INSERT INTO episodes(agent, content) VALUES ('user', ?)",
            ("QuartzPreexistingBackfill",),
        )
        conn.execute("DELETE FROM knowledge_index_jobs")

    monkeypatch.setattr(
        worker,
        "process_knowledge_embeddings",
        lambda limit: {
            "status": "unavailable",
            "selected": 0,
            "indexed": 0,
            "failed": 0,
        },
    )
    report = worker.run_knowledge_maintenance_once(
        backfill_limit=100,
        job_limit=100,
        embedding_limit=1,
    )

    rows, backend = search_knowledge_items(
        "QuartzPreexistingBackfill",
        source_types=("note",),
    )
    assert report["status"] == "ok"
    assert backend == "fts"
    assert len(rows) == 1


def test_knowledge_embeddings_are_produced_consumed_and_invalidated(
    knowledge_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config
    from database.knowledge import (
        delete_knowledge_item,
        get_knowledge_embeddings,
        get_missing_knowledge_embeddings,
        upsert_knowledge_item,
    )
    from jarvis.retrieval import (
        RetrievalRequest,
        process_knowledge_embeddings,
        search_knowledge,
    )
    from scripts import semantic_search

    uid = upsert_knowledge_item(
        source_type="fact",
        source_id="semantic-current",
        searchable_text="Le vélo bleu est rangé dans la cave.",
    )
    monkeypatch.setattr(semantic_search, "embed_text", lambda _text: [1.0])
    monkeypatch.setattr(semantic_search, "embedding_to_blob", lambda _vector: b"v1")
    monkeypatch.setattr(semantic_search, "blob_to_embedding", lambda _blob: [1.0])
    monkeypatch.setattr(
        semantic_search,
        "cosine_similarity",
        lambda _left, _right: 0.91,
    )

    report = process_knowledge_embeddings(limit=10)
    assert report["indexed"] == 1
    assert len(get_knowledge_embeddings(model=config.SEMANTIC_SEARCH_MODEL)) == 1

    result = search_knowledge(
        RetrievalRequest(
            query="Où ai-je remisé mon moyen de transport azur ?",
            source_types=("fact",),
        )
    )
    assert result.hits[0].uid == uid
    assert "semantic" in result.hits[0].reasons

    upsert_knowledge_item(
        source_type="fact",
        source_id="semantic-current",
        searchable_text="Le vélo est maintenant dans le garage.",
    )
    assert get_knowledge_embeddings(model=config.SEMANTIC_SEARCH_MODEL) == []
    assert (
        len(
            get_missing_knowledge_embeddings(
                model=config.SEMANTIC_SEARCH_MODEL,
                limit=10,
            )
        )
        == 1
    )
    assert delete_knowledge_item("fact", "semantic-current") == 1
    assert (
        get_missing_knowledge_embeddings(
            model=config.SEMANTIC_SEARCH_MODEL,
            limit=10,
        )
        == []
    )


def test_knowledge_semantic_embeddings_respect_person_and_time_filters(
    knowledge_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config
    from database.knowledge import get_knowledge_embeddings, upsert_knowledge_item
    from jarvis.retrieval import (
        RetrievalRequest,
        process_knowledge_embeddings,
        search_knowledge,
    )
    from scripts import semantic_search

    expected_uid = upsert_knowledge_item(
        source_type="fact",
        source_id="semantic-gregoire-current",
        searchable_text="Preuve courante sans vocabulaire de requête",
        people=("Grégoire",),
        occurred_at="2026-08-15T10:00:00Z",
    )
    upsert_knowledge_item(
        source_type="fact",
        source_id="semantic-gregoire-old",
        searchable_text="Preuve ancienne sans vocabulaire de requête",
        people=("Grégoire",),
        occurred_at="2026-08-01T10:00:00Z",
    )
    upsert_knowledge_item(
        source_type="fact",
        source_id="semantic-alice-current",
        searchable_text="Preuve Alice sans vocabulaire de requête",
        people=("Alice",),
        occurred_at="2026-08-15T10:00:00Z",
    )
    monkeypatch.setattr(semantic_search, "embed_text", lambda _text: [1.0])
    monkeypatch.setattr(semantic_search, "embedding_to_blob", lambda _vector: b"v1")
    monkeypatch.setattr(semantic_search, "blob_to_embedding", lambda _blob: [1.0])
    monkeypatch.setattr(
        semantic_search,
        "cosine_similarity",
        lambda _left, _right: 0.91,
    )
    assert process_knowledge_embeddings(limit=10)["indexed"] == 3

    result = search_knowledge(
        RetrievalRequest(
            query="paraphrase totalement étrangère",
            source_types=("fact",),
            person="Grégoire",
            from_iso="2026-08-15T00:00:00Z",
            to_iso="2026-08-15T23:59:59Z",
        )
    )

    assert [hit.uid for hit in result.hits] == [expected_uid]
    assert "semantic" in result.hits[0].reasons
    assert (
        len(
            get_knowledge_embeddings(
                model=config.SEMANTIC_SEARCH_MODEL,
                person="Grégoire",
                from_iso="2026-08-15T00:00:00Z",
                to_iso="2026-08-15T23:59:59Z",
            )
        )
        == 1
    )


def test_resumed_backfill_keeps_cumulative_source_count(knowledge_db: Path) -> None:
    from database import get_db
    from database.knowledge import get_knowledge_source_states
    from jarvis.retrieval import backfill_knowledge

    with get_db() as conn:
        conn.executemany(
            "INSERT INTO episodes(agent, content) VALUES ('user', ?)",
            [("Lot un",), ("Lot deux",), ("Lot trois",)],
        )
        conn.execute("DELETE FROM knowledge_index_jobs")

    first = backfill_knowledge(
        source_types=("note",),
        batch_size=2,
        max_items=2,
        resume=True,
    )
    second = backfill_knowledge(
        source_types=("note",),
        batch_size=2,
        max_items=2,
        resume=True,
    )
    states = {row["source_key"]: row for row in get_knowledge_source_states(("note",))}
    assert first["indexed"] == 2
    assert second["indexed"] == 1
    assert states["user_notes"]["item_count"] == 3

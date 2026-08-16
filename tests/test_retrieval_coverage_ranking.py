"""Non-régressions coverage, compréhension et ranking du retrieval universel."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


def test_fresh_unbound_live_source_is_unavailable_not_verified(
    knowledge_db: Path,
) -> None:
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    result = search_knowledge(
        RetrievalRequest(query="mail de Grégoire", source_types=("email",))
    )

    assert result.hits == ()
    assert result.status == "unavailable"
    assert result.verified_sources == ()
    assert result.partial_sources == ()
    assert result.unavailable_sources == ("email",)
    assert result.source_coverage[0].status == "unavailable"
    assert result.source_coverage[0].reason == "connector_unbound"


def test_bound_but_incomplete_live_source_is_partial(knowledge_db: Path) -> None:
    from database.ingestion import (
        bind_connector,
        list_ingestion_jobs,
        update_ingestion_source_state,
    )
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    bind_connector("mail")
    update_ingestion_source_state(
        "mail",
        status="idle",
        completeness="partial",
        coverage_start_utc="2026-08-15T00:00:00Z",
        coverage_end_utc="2026-08-16T00:00:00Z",
    )
    result = search_knowledge(
        RetrievalRequest(query="mail de Grégoire", source_types=("email",))
    )

    assert result.hits == ()
    assert result.status == "degraded"
    assert result.verified_sources == ()
    assert result.partial_sources == ("email",)
    assert result.source_coverage[0].status == "partial"
    jobs = list_ingestion_jobs(source="mail")
    assert len(jobs) == 1
    assert jobs[0].job_kind == "sync"


def test_empty_live_source_is_verified_only_inside_complete_coverage(
    knowledge_db: Path,
) -> None:
    from database.ingestion import bind_connector, update_ingestion_source_state
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    bind_connector("calendar")
    update_ingestion_source_state(
        "calendar",
        status="idle",
        completeness="complete",
        last_success_at=datetime.now(timezone.utc).isoformat(),
        coverage_start_utc="2026-08-15T00:00:00Z",
        coverage_end_utc="2026-08-16T00:00:00Z",
    )

    covered = search_knowledge(
        RetrievalRequest(
            query="agenda",
            source_types=("calendar",),
            from_iso="2026-08-15T08:00:00Z",
            to_iso="2026-08-15T09:00:00Z",
        )
    )
    outside = search_knowledge(
        RetrievalRequest(
            query="agenda",
            source_types=("calendar",),
            from_iso="2026-08-14T08:00:00Z",
            to_iso="2026-08-14T09:00:00Z",
        )
    )

    assert covered.status == "ok"
    assert covered.verified_sources == ("calendar",)
    assert covered.source_coverage[0].status == "complete"
    assert outside.status == "degraded"
    assert outside.verified_sources == ()
    assert outside.partial_sources == ("calendar",)


def test_imessage_display_name_survives_handle_and_distractors(
    knowledge_db: Path,
) -> None:
    from database import get_db
    from database.ingestion import (
        bind_connector,
        update_ingestion_source_state,
        upsert_contact_identity,
    )
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    upsert_contact_identity(
        "phone",
        "+33612345678",
        display_name="Grégoire",
        source="contacts",
    )
    bind_connector("imessage")
    update_ingestion_source_state(
        "imessage",
        status="idle",
        completeness="complete",
        last_success_at=datetime.now(timezone.utc).isoformat(),
    )
    with get_db() as conn:
        handle_id = conn.execute(
            "INSERT INTO imessage_handles(apple_handle_id, handle) VALUES (?, ?)",
            (7_001, "+33612345678"),
        ).lastrowid
        chat_id = conn.execute(
            """
            INSERT INTO imessage_chats(
                apple_chat_id, chat_identifier, display_name
            ) VALUES (?, ?, ?)
            """,
            (8_001, "chat-gregoire", None),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO imessage_messages(
                apple_rowid, guid, chat_id, handle_id, text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                9_001,
                "imessage-target",
                chat_id,
                handle_id,
                "Le dossier Orion est validé.",
                "2026-08-01T10:00:00Z",
            ),
        )
        for index in range(2, 16):
            conn.execute(
                """
                INSERT INTO imessage_messages(
                    apple_rowid, guid, text, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    9_000 + index,
                    f"imessage-noise-{index}",
                    f"Un message banal numéro {index}.",
                    f"2026-08-{index:02d}T12:00:00Z",
                ),
            )

    result = search_knowledge(
        RetrievalRequest(
            query="qu'est-ce que Grégoire m'a écrit en message ?",
            source_types=("imessage",),
        )
    )

    assert result.verified_sources == ("imessage",)
    assert [hit.metadata.get("guid") for hit in result.hits] == ["imessage-target"]
    assert result.hits[0].title == "Grégoire"
    assert result.candidate_count <= 20


def test_imessage_attachment_only_content_is_searchable(
    knowledge_db: Path,
) -> None:
    from database import get_db
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    with get_db() as conn:
        message_id = conn.execute(
            """INSERT INTO imessage_messages(apple_rowid, guid, text, created_at)
               VALUES (?, ?, NULL, ?)""",
            (99_001, "attachment-only", "2026-08-15T10:00:00Z"),
        ).lastrowid
        attachment_id = conn.execute(
            """INSERT INTO imessage_attachments(
                   apple_attachment_id, guid, filename, mime_type, transfer_name
               ) VALUES (?, ?, ?, ?, ?)""",
            (88_001, "audio-guid", "note-vocale.m4a", "audio/mp4", "Orion audio"),
        ).lastrowid
        conn.execute(
            """INSERT INTO imessage_message_attachments(message_id, attachment_id)
               VALUES (?, ?)""",
            (message_id, attachment_id),
        )

    result = search_knowledge(
        RetrievalRequest(
            query="note vocale Orion audio",
            source_types=("imessage",),
        )
    )

    assert [hit.metadata.get("guid") for hit in result.hits] == ["attachment-only"]
    assert result.hits[0].metadata["attachment_count"] == 1


def test_impersonal_yesterday_query_stays_broad(knowledge_db: Path) -> None:
    from database import get_db
    from database.ingestion import bind_connector, list_ingestion_jobs
    from database.knowledge import upsert_calendar_events
    from database.time_buckets import local_datetime, utc_bounds_for_local_day
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    yesterday = local_datetime().date() - timedelta(days=1)
    start, _end = utc_bounds_for_local_day(yesterday)
    calendar_start = start.replace(" ", "T") + "Z"
    for source in ("mail", "calendar", "imessage"):
        bind_connector(source)
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO email_summaries(
                gmail_id, sender, subject, body, received_at, is_read
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("mail-yesterday", "Alice", "Mail hier", "Une nouvelle.", start, 1),
        )
        handle_id = conn.execute(
            "INSERT INTO imessage_handles(apple_handle_id, handle) VALUES (?, ?)",
            (101, "alice@example.test"),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO imessage_messages(
                apple_rowid, guid, handle_id, text, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (101, "message-yesterday", handle_id, "Une réponse hier.", start),
        )
    upsert_calendar_events(
        [
            {
                "id": "calendar-yesterday",
                "calendar": "Personnel",
                "title": "Rendez-vous hier",
                "start": calendar_start,
                "end": calendar_start,
            }
        ]
    )

    request = RetrievalRequest(
        query="il s'est passé quoi hier ?",
        recent_user_turns=("résume mes mails",),
    )
    assert "résume mes mails" not in request.effective_query

    result = search_knowledge(request)
    sources = {hit.source_type for hit in result.hits}
    assert {"email", "calendar", "imessage"} <= sources
    assert result.candidate_count <= 20
    assert {
        job.source
        for source in ("mail", "calendar", "imessage")
        for job in list_ingestion_jobs(source=source)
    } == {"mail", "calendar", "imessage"}


def test_latest_n_is_structured_and_strictly_chronological(
    knowledge_db: Path,
) -> None:
    from database import get_db
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    with get_db() as conn:
        for index in range(1, 6):
            conn.execute(
                """
                INSERT INTO email_summaries(
                    gmail_id, sender, subject, body, received_at, is_read
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"mail-{index}",
                    "Alice",
                    f"Sujet {index}",
                    "Contenu",
                    f"2026-08-{index:02d}T10:00:00Z",
                    1,
                ),
            )

    result = search_knowledge(
        RetrievalRequest(
            query="je veux lire mes 3 derniers mails",
            source_types=("email",),
        )
    )
    assert [hit.title for hit in result.hits] == ["Sujet 5", "Sujet 4", "Sujet 3"]


def test_absolute_french_date_is_a_structured_filter(knowledge_db: Path) -> None:
    from database import get_db
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    with get_db() as conn:
        conn.execute(
            "INSERT INTO jarvis_journal(date, entry) VALUES (?, ?)",
            ("2026-06-04", "Décision Atlas"),
        )
        conn.execute(
            "INSERT INTO jarvis_journal(date, entry) VALUES (?, ?)",
            ("2026-06-05", "Décision hors fenêtre"),
        )

    result = search_knowledge(
        RetrievalRequest(
            query="journal du 4 juin 2026",
            source_types=("journal",),
        )
    )
    assert len(result.hits) == 1
    assert "Atlas" in result.hits[0].excerpt


def test_absolute_french_date_drives_live_calendar_window() -> None:
    from jarvis.retrieval import RetrievalRequest
    from jarvis.retrieval.coordinator import prepare_retrieval_request
    from jarvis.retrieval.live_sources import _calendar_window

    request = prepare_retrieval_request(RetrievalRequest(query="agenda du 4 juin 2026"))
    start, end = _calendar_window(
        request.query,
        from_iso=request.from_iso,
        to_iso=request.to_iso,
    )

    assert start.date().isoformat() == "2026-06-04"
    assert end.date().isoformat() == "2026-06-05"


def test_missing_embeddings_mix_hot_and_backfill(knowledge_db: Path) -> None:
    from database import get_db
    from database.knowledge import (
        get_missing_knowledge_embeddings,
        upsert_knowledge_item,
    )

    for index in range(1, 21):
        uid = upsert_knowledge_item(
            source_type="note",
            source_id=f"embedding-{index}",
            title=f"Embedding {index}",
            searchable_text=f"Contenu {index}",
        )
        with get_db() as conn:
            conn.execute(
                "UPDATE knowledge_items SET indexed_at = ? WHERE uid = ?",
                (f"2026-01-{index:02d}T00:00:00Z", uid),
            )

    rows = get_missing_knowledge_embeddings(model="audit-model", limit=10)
    source_ids = [row["uid"].split(":")[1] for row in rows]
    assert source_ids[:8] == [
        "embedding-20",
        "embedding-19",
        "embedding-18",
        "embedding-17",
        "embedding-16",
        "embedding-15",
        "embedding-14",
        "embedding-13",
    ]
    assert source_ids[8:] == ["embedding-1", "embedding-2"]


def test_partial_source_with_a_hit_is_not_reported_as_verified(
    knowledge_db: Path,
) -> None:
    from database import get_db
    from database.ingestion import bind_connector, update_ingestion_source_state
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    bind_connector("mail")
    update_ingestion_source_state(
        "mail",
        status="idle",
        completeness="partial",
        coverage_start_utc="2026-08-15T00:00:00Z",
        coverage_end_utc="2026-08-16T00:00:00Z",
    )
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO email_summaries(
                gmail_id, sender, subject, body, received_at, is_read
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "partial-gregoire",
                "Grégoire",
                "Accord Orion",
                "Le dossier est prêt.",
                "2026-08-15T10:00:00Z",
                1,
            ),
        )

    result = search_knowledge(
        RetrievalRequest(query="mail Orion", source_types=("email",))
    )

    assert len(result.hits) == 1
    assert result.status == "degraded"
    assert result.verified_sources == ()
    assert result.partial_sources == ("email",)


def test_common_m_as_typo_still_filters_the_named_sender(
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
                "gregoire-target",
                "Grégoire Dupont",
                "Accord Orion",
                "Voici le message personnel attendu.",
                "2026-06-01T10:00:00Z",
                1,
            ),
        )
        for index in range(25):
            conn.execute(
                """
                INSERT INTO email_summaries(
                    gmail_id, sender, subject, body, received_at, is_read
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"distractor-{index}",
                    f"Expéditeur {index}",
                    "Message banal",
                    "Aucun rapport avec Orion.",
                    f"2026-07-{index + 1:02d}T10:00:00Z",
                    1,
                ),
            )

    result = search_knowledge(
        RetrievalRequest(
            query="grégoire ne m'as pas envoyer de mail ?",
            source_types=("email",),
        )
    )

    assert len(result.hits) == 1
    assert result.hits[0].metadata.get("external_id") == "gregoire-target"


def test_broad_timeline_keeps_live_sources_in_the_final_eight() -> None:
    from jarvis.retrieval import RetrievalHit, RetrievalRequest
    from jarvis.retrieval.coordinator import (
        _apply_result_budget,
        _rank_candidates_bounded,
    )

    email_hits = [
        RetrievalHit(
            uid=f"email:mail-{index}:0",
            source_type="email",
            source_id=f"mail-{index}",
            title=f"Mail {index}",
            excerpt="Contenu mail",
            occurred_at=f"2026-08-15T{20 - index:02d}:00:00Z",
            score=100.0 - index,
        )
        for index in range(8)
    ]
    other_hits = [
        RetrievalHit(
            uid="calendar:event-1:0",
            source_type="calendar",
            source_id="event-1",
            title="Rendez-vous",
            excerpt="Événement calendrier",
            occurred_at="2026-08-15T08:00:00Z",
            score=20.0,
        ),
        RetrievalHit(
            uid="imessage:message-1:0",
            source_type="imessage",
            source_id="message-1",
            title="Grégoire",
            excerpt="Message reçu",
            occurred_at="2026-08-15T07:00:00Z",
            score=10.0,
        ),
    ]
    request = RetrievalRequest(query="il s'est passé quoi hier ?")

    ranked = _rank_candidates_bounded(
        [*email_hits, *other_hits],
        request,
        ("email", "calendar", "imessage"),
    )
    final = _apply_result_budget(ranked, request.max_hits, request.char_budget)

    assert {hit.source_type for hit in final} == {"email", "calendar", "imessage"}

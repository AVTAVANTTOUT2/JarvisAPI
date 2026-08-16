"""E2E local : données persistées -> retrieval -> contexte LLM borné."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

YESTERDAY = "2026-08-15"


@pytest.fixture()
def universal_memory_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    import config
    import database

    db_path = tmp_path / "universal-memory-e2e.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    _seed_universal_memory()
    return db_path


def _seed_universal_memory() -> None:
    from database import get_db
    from database.email import save_email_full
    from database.knowledge import upsert_calendar_events

    emails = (
        (
            "mail-archive",
            "Archives <archives@example.test>",
            "Ancienne archive",
            "Message antérieur qui ne doit pas entrer dans les trois derniers.",
            "2026-08-13T07:00:00+00:00",
            False,
        ),
        (
            "mail-preparation",
            "Camille <camille@example.test>",
            "Préparation de la semaine",
            "Troisième message récent.",
            f"{YESTERDAY}T07:00:00+00:00",
            False,
        ),
        (
            "mail-gregoire-read",
            "Grégoire <gregoire@example.test>",
            "Accord Orion",
            "Hier, Grégoire a confirmé la validation Orion. Ce mail est déjà lu.",
            f"{YESTERDAY}T09:00:00+00:00",
            True,
        ),
        (
            "mail-report",
            "Nora <nora@example.test>",
            "Rapport final",
            "Message le plus récent.",
            f"{YESTERDAY}T12:00:00+00:00",
            True,
        ),
    )
    for gmail_id, sender, subject, body, received_at, is_read in emails:
        save_email_full(
            gmail_id=gmail_id,
            sender=sender,
            subject=subject,
            body=body,
            received_at=received_at,
            summary=body,
            is_read=is_read,
        )

    with get_db() as conn:
        handle_id = conn.execute(
            "INSERT INTO imessage_handles(apple_handle_id, handle) VALUES (?, ?)",
            (91, "gregoire@example.test"),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO imessage_messages(
                apple_rowid, guid, handle_id, text, is_read, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                91,
                "imessage-orion-yesterday",
                handle_id,
                "Hier, Grégoire a confirmé Orion par iMessage.",
                1,
                f"{YESTERDAY}T10:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO recordings(
                label, title, duration_seconds, transcription, summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "orion",
                "Note vocale Orion",
                42,
                "Hier, la note vocale rappelle la décision Orion de Grégoire.",
                "Décision Orion enregistrée hier.",
                f"{YESTERDAY}T11:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO episodes(agent, content, summary, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                "user",
                "Ancienne note Borealis : conserver la décision historique.",
                "Ancienne note Borealis",
                "2024-01-02T08:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO dev_projects(
                slug, name, isolation_path, project_type, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "borealis",
                "Projet Borealis",
                "/tmp/borealis",
                "assistant",
                "active",
                "2026-08-10T08:00:00Z",
                "2026-08-15T18:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO agent_runs(
                run_id, profile_id, origin, channel, runtime_id, status, phase,
                category, title, budget_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-borealis",
                "default",
                "user",
                "chat",
                "runtime-test",
                "paused",
                "review",
                "agentic_readonly",
                "État agent Borealis",
                "{}",
                "2026-08-15T18:30:00Z",
                "2026-08-15T19:00:00Z",
            ),
        )

    assert (
        upsert_calendar_events(
            [
                {
                    "external_id": "calendar-orion-yesterday",
                    "calendar_name": "Travail",
                    "title": "Point Orion avec Grégoire",
                    "start_at": f"{YESTERDAY}T15:00:00+00:00",
                    "end_at": f"{YESTERDAY}T16:00:00+00:00",
                    "location": "Lille",
                    "notes": "Hier, validation Orion avec Grégoire.",
                }
            ]
        )
        == 1
    )


def _decode_retrieval_context(block: str) -> dict:
    assert block.startswith("[UNTRUSTED_DATA:KNOWLEDGE_RETRIEVAL]")
    envelope = json.loads(block.splitlines()[1])
    assert envelope["source"] == "KNOWLEDGE_RETRIEVAL"
    return json.loads(envelope["content"])


@pytest.mark.asyncio
async def test_seed_to_chat_context_recalls_yesterdays_cross_source_data(
    universal_memory_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import chat_context

    live_calls = []

    async def _no_live_dependency(request):
        live_calls.append(request)
        return {}

    monkeypatch.setattr(chat_context, "refresh_live_sources", _no_live_dependency)
    monkeypatch.setattr(
        chat_context,
        "get_conversation_history",
        lambda _conversation_id, *, limit: [],
    )

    context: dict = {}
    await chat_context._attach_retrieval_context(
        context,
        text="Qu'est-il arrivé hier sur Orion avec Grégoire ?",
        conversation_id=41,
        interaction_mode="chat",
    )

    assert live_calls == []
    payload = _decode_retrieval_context(context["retrieval_context"])
    hits_by_source = {hit["source_type"]: hit for hit in payload["hits"]}
    assert {"email", "imessage", "calendar", "recording"} <= set(hits_by_source)
    for source_type in ("email", "imessage", "calendar", "recording"):
        assert hits_by_source[source_type]["uid"].startswith(f"{source_type}:")

    references = context["__retrieval_references"]
    assert {item["source_type"] for item in references} >= {
        "email",
        "imessage",
        "calendar",
        "recording",
    }


def test_formatted_context_preserves_yesterday_dates_for_every_source(
    universal_memory_db: Path,
) -> None:
    from jarvis.retrieval import (
        RetrievalRequest,
        format_retrieval_context,
        search_knowledge,
    )

    result = search_knowledge(
        RetrievalRequest(
            query="Orion Grégoire hier",
            source_types=("email", "imessage", "calendar", "recording"),
            max_hits=8,
        )
    )
    raw_hits = {hit.source_type: hit for hit in result.hits}
    assert all(
        str(raw_hits[source_type].occurred_at).startswith(YESTERDAY)
        for source_type in ("email", "imessage", "calendar", "recording")
    )
    payload = _decode_retrieval_context(format_retrieval_context(result))
    hits_by_source = {hit["source_type"]: hit for hit in payload["hits"]}
    invalid_dates = {
        source_type: hits_by_source[source_type]["occurred_at"]
        for source_type in ("email", "imessage", "calendar", "recording")
        if not str(hits_by_source[source_type]["occurred_at"]).startswith(YESTERDAY)
    }

    assert not invalid_dates, invalid_dates


def test_read_gregoire_mail_is_searchable(
    universal_memory_db: Path,
) -> None:
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    result = search_knowledge(
        RetrievalRequest(
            query="Grégoire Orion",
            source_types=("email",),
            max_hits=3,
        )
    )

    assert len(result.hits) == 1
    assert result.hits[0].title == "Accord Orion"
    assert result.hits[0].metadata["is_read"] == 1


def test_read_gregoire_mail_identity_reaches_context(
    universal_memory_db: Path,
) -> None:
    from jarvis.retrieval import (
        RetrievalRequest,
        format_retrieval_context,
        search_knowledge,
    )

    result = search_knowledge(
        RetrievalRequest(
            query="Grégoire Orion",
            source_types=("email",),
            max_hits=3,
        )
    )
    payload = _decode_retrieval_context(format_retrieval_context(result))
    mail_context = payload["hits"][0]
    rendered = f"{mail_context['title']} {mail_context['excerpt']}"

    assert "Grégoire" in rendered and "Orion" in rendered, mail_context


def test_three_latest_mails_include_read_gregoire_mail_in_order(
    universal_memory_db: Path,
) -> None:
    from jarvis.retrieval import (
        RetrievalRequest,
        format_retrieval_context,
        search_knowledge,
    )

    result = search_knowledge(
        RetrievalRequest(
            query="résume mes 3 derniers mails",
            source_types=("email",),
            max_hits=3,
        )
    )

    assert [hit.title for hit in result.hits] == [
        "Rapport final",
        "Accord Orion",
        "Préparation de la semaine",
    ], [(hit.title, hit.occurred_at, hit.score, hit.reasons) for hit in result.hits]

    payload = _decode_retrieval_context(format_retrieval_context(result))
    assert len(payload["hits"]) == 3
    # Les titres peuvent être pseudonymisés par la frontière PII ; l'ordre et
    # les références canoniques, eux, doivent rester déterministes.
    formatted_dates = [str(hit["occurred_at"]) for hit in payload["hits"]]
    assert formatted_dates == sorted(formatted_dates, reverse=True)
    assert [value[-8:] for value in formatted_dates] == [
        "12:00:00",
        "09:00:00",
        "07:00:00",
    ]


def test_project_and_agent_state_are_searchable_and_reach_context(
    universal_memory_db: Path,
) -> None:
    from jarvis.retrieval import (
        RetrievalRequest,
        format_retrieval_context,
        search_knowledge,
    )

    result = search_knowledge(
        RetrievalRequest(
            query="Borealis",
            source_types=("project", "agent_run"),
            max_hits=4,
        )
    )

    hits = {hit.source_type: hit for hit in result.hits}
    assert set(hits) == {"project", "agent_run"}
    assert "active" in hits["project"].excerpt
    assert "paused" in hits["agent_run"].excerpt
    assert "review" in hits["agent_run"].excerpt

    payload = _decode_retrieval_context(format_retrieval_context(result))
    assert {hit["source_type"] for hit in payload["hits"]} == {
        "project",
        "agent_run",
    }


def test_old_note_is_searchable_through_canonical_note_source(
    universal_memory_db: Path,
) -> None:
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    result = search_knowledge(
        RetrievalRequest(query="Borealis", source_types=("note",))
    )

    assert result.status == "ok"
    assert result.verified_sources == ("note",)
    assert len(result.hits) == 1
    assert result.hits[0].source_type == "note"
    assert result.hits[0].occurred_at == "2024-01-02T08:00:00Z"

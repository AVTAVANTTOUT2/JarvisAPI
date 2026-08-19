"""Chapitres mensuels par personne, retrieval identité/histoire, curseur d'extraction."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture()
def history_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import config
    import database

    db_path = tmp_path / "jarvis.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    return db_path


def _seed_ada_with_noise(conn) -> int:
    person_id = conn.execute(
        "INSERT INTO people(name, relationship, ai_description, imessage_count) "
        "VALUES (?, ?, ?, ?)",
        ("Ada", "amie", "Ada est une amie de longue date.", 40),
    ).lastrowid
    conv_id = conn.execute(
        "INSERT INTO conversations(title, summary) VALUES (?, ?)",
        ("qui est Ada", "Une vieille conversation JARVIS"),
    ).lastrowid
    conn.execute(
        "INSERT INTO messages(conversation_id, role, content) VALUES (?, 'user', ?)",
        (conv_id, "qui est Ada"),
    )
    handle_id = conn.execute(
        "INSERT INTO imessage_handles(apple_handle_id, handle) VALUES (?, ?)",
        (42, "+33600000001"),
    ).lastrowid
    conn.execute(
        "INSERT INTO relationship_profiles(person_id, handle) VALUES (?, ?)",
        (person_id, "+33600000001"),
    )
    conn.execute(
        """
        INSERT INTO imessage_messages(
            apple_rowid, guid, handle_id, text, occurred_at_utc, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            101,
            "guid-ada-101",
            handle_id,
            "Ada prend l'avion pour le Portugal demain",
            "2026-01-15T10:00:00Z",
            "2026-01-15T10:00:00Z",
        ),
    )
    return int(person_id)


def test_extract_structured_person_on_identity_and_history_phrases(
    history_db: Path,
) -> None:
    from jarvis.retrieval.coordinator import _extract_structured_person

    assert _extract_structured_person("qui est Ada") == "Ada"
    assert _extract_structured_person("c'est qui Ada") == "Ada"
    assert _extract_structured_person("c est qui Ada") == "Ada"
    assert _extract_structured_person("histoire avec Ada") == "Ada"
    assert _extract_structured_person("ce qui s'est passé avec Ada") == "Ada"
    assert _extract_structured_person("messages avec Ada") == "Ada"
    assert _extract_structured_person("qui est le président") is None


def test_person_query_kind_requires_an_extracted_person() -> None:
    from jarvis.retrieval.coordinator import _person_query_kind

    assert _person_query_kind("qui est Ada") == "identity"
    assert _person_query_kind("histoire avec Ada") == "history"
    assert _person_query_kind("qui est le président") is None
    assert _person_query_kind("raconte une histoire") is None
    assert _person_query_kind("fiche d'histoire de France") is None


def test_identity_query_prefers_person_dossier_over_imessage_and_jarvis_chats(
    history_db: Path,
) -> None:
    from database import get_db
    from jarvis.retrieval import RetrievalRequest, search_knowledge
    from jarvis.retrieval.coordinator import _extract_structured_person

    with get_db() as conn:
        _seed_ada_with_noise(conn)

    assert _extract_structured_person("qui est Ada") == "Ada"
    result = search_knowledge(RetrievalRequest(query="qui est Ada", max_hits=8))
    types = [hit.source_type for hit in result.hits]
    assert "person" in types
    assert "imessage" not in types
    assert "conversation" not in types
    assert "message" not in types


def test_mac_sync_does_not_advance_extraction_cursor(history_db: Path) -> None:
    from database.people import (
        force_upsert_people_from_mac_sync,
        get_analysis_cursor,
        get_total_messages_analyzed,
    )

    force_upsert_people_from_mac_sync(
        [
            {
                "handle": "+33600000001",
                "name": "Ada",
                "msg_count": 5095,
                "last_rowid": 40645,
                "last_message_at": "2026-08-19T10:00:00Z",
            }
        ]
    )
    assert get_analysis_cursor("+33600000001") == 0
    assert get_total_messages_analyzed("+33600000001") == 0


def test_empty_month_persists_empty_chapter_without_llm(
    history_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from database import get_db
    from database.person_history import get_chapter

    calls: list[object] = []

    async def _forbidden(*_args, **_kwargs):
        calls.append(1)
        raise AssertionError("aucun appel LLM pour un mois vide")

    monkeypatch.setattr("llm.chat", _forbidden)

    with get_db() as conn:
        person_id = conn.execute(
            "INSERT INTO people(name) VALUES ('Ada')"
        ).lastrowid

    import asyncio
    from scripts.person_history import build_chapter

    result = asyncio.run(build_chapter(int(person_id), "2026-02"))
    assert result["status"] == "empty"
    assert result["message_count"] == 0
    assert calls == []
    stored = get_chapter(int(person_id), "2026-02")
    assert stored is not None
    assert stored["status"] == "empty"


def test_unchanged_hash_skips_llm(
    history_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from database import get_db
    from scripts.person_history import build_chapter

    calls = {"n": 0}

    async def _chat(**_kwargs):
        calls["n"] += 1
        return {
            "content": json.dumps(
                {
                    "highlights": [
                        {
                            "apple_rowid": 101,
                            "occurred_at_utc": "2026-01-15T10:00:00Z",
                            "quote": "On se voit mardi",
                            "kind": "plan",
                        }
                    ],
                    "narrative": "Janvier a été calme.",
                    "mood_arc": "stable",
                }
            ),
            "tokens_in": 12,
            "tokens_out": 30,
            "cost": 0.0,
            "model": "deepseek-v4-flash",
        }

    monkeypatch.setattr("llm.chat", _chat)

    with get_db() as conn:
        person_id = _seed_ada_with_noise(conn)

    first = asyncio.run(build_chapter(int(person_id), "2026-01"))
    second = asyncio.run(build_chapter(int(person_id), "2026-01"))
    assert first["status"] == "complete"
    assert second.get("skipped") is True
    assert calls["n"] == 1


def test_invalid_llm_json_falls_back_to_deterministic_partial(
    history_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from database import get_db
    from scripts.person_history import build_chapter

    async def _chat(**_kwargs):
        return {"content": "pas du json", "tokens_in": 4, "tokens_out": 4, "cost": 0}

    monkeypatch.setattr("llm.chat", _chat)

    with get_db() as conn:
        person_id = _seed_ada_with_noise(conn)

    result = asyncio.run(build_chapter(int(person_id), "2026-01"))
    assert result["status"] == "partial"
    assert result["narrative"]
    assert "Janvier" in result["narrative"] or "2026-01" in result["narrative"]


def test_history_query_uses_month_chapters_not_raw_imessage(history_db: Path) -> None:
    from database import get_db
    from database.person_history import upsert_chapter
    from jarvis.retrieval import RetrievalRequest, search_knowledge

    with get_db() as conn:
        person_id = _seed_ada_with_noise(conn)
    upsert_chapter(
        person_id=int(person_id),
        year_month="2026-01",
        status="complete",
        message_count=1,
        sent_count=0,
        recv_count=1,
        highlights=[
            {
                "apple_rowid": 101,
                "occurred_at_utc": "2026-01-15T10:00:00Z",
                "quote": "Ada prend l'avion",
                "kind": "plan",
            }
        ],
        narrative="En janvier, Ada est partie au Portugal.",
        mood_arc="calme",
        source_rowid_min=101,
        source_rowid_max=101,
        content_hash="abc",
        period_start_utc="2026-01-01T00:00:00Z",
        period_end_utc="2026-02-01T00:00:00Z",
    )

    result = search_knowledge(
        RetrievalRequest(query="histoire avec Ada", max_hits=8)
    )
    types = [hit.source_type for hit in result.hits]
    assert "person_month" in types
    assert "imessage" not in types


def test_missing_history_enqueues_ingestion_job(history_db: Path) -> None:
    from database import get_db
    from database.ingestion import bind_connector, list_ingestion_jobs
    from scripts.person_history import ensure_history_coverage

    bind_connector("imessage", permission_state="granted")
    with get_db() as conn:
        _seed_ada_with_noise(conn)

    pending = ensure_history_coverage("Ada")
    assert pending is True
    jobs = list_ingestion_jobs(source="person_history")
    assert jobs
    assert jobs[0].job_kind == "chapter"


def test_opencode_and_agentic_do_not_write_person_month_chapters() -> None:
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    needles = (
        "person_month_chapters",
        "database.person_history",
        "upsert_chapter",
    )
    for rel in ("integrations/opencode", "jarvis/agentic"):
        directory = root / rel
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if any(needle in text for needle in needles):
                offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_person_history_job_never_opens_chat_db() -> None:
    text = (
        Path(__file__).resolve().parents[1] / "scripts" / "person_history.py"
    ).read_text(encoding="utf-8")
    assert "chat.db" not in text
    assert "Library/Messages" not in text
    assert "AppleDataService" not in text


def test_chapter_updated_event_is_declared_without_shifting_domain_events() -> None:
    from jarvis.event_bus import DOMAIN_EVENT_TYPES, VALID_EVENT_TYPES
    from jarvis.events import DOMAIN_EVENT_CLASSES

    assert "person.chapter_updated" in VALID_EVENT_TYPES
    assert "person.chapter_updated" not in DOMAIN_EVENT_TYPES
    assert tuple(event.EVENT_TYPE for event in DOMAIN_EVENT_CLASSES) == DOMAIN_EVENT_TYPES

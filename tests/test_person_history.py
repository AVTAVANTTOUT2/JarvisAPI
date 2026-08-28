"""Chapitres mensuels par personne, retrieval identité/histoire, curseur d'extraction."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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


def test_complete_chapter_not_downgraded_when_llm_fails_on_rebuild(
    history_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from database import get_db
    from scripts.person_history import build_chapter

    good_narrative = "Janvier a été riche en échanges."

    async def _chat_ok(**_kwargs):
        return {
            "content": json.dumps(
                {
                    "highlights": [],
                    "narrative": good_narrative,
                    "mood_arc": "stable",
                }
            ),
            "tokens_in": 12,
            "tokens_out": 30,
            "cost": 0.0,
            "model": "deepseek-v4-flash",
        }

    monkeypatch.setattr("llm.chat", _chat_ok)

    with get_db() as conn:
        person_id = _seed_ada_with_noise(conn)
        handle_id = conn.execute(
            "SELECT handle_id FROM imessage_messages WHERE apple_rowid = 101"
        ).fetchone()[0]

    first = asyncio.run(build_chapter(int(person_id), "2026-01"))
    assert first["status"] == "complete"
    assert first["narrative"] == good_narrative

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO imessage_messages(
                apple_rowid, guid, handle_id, text, occurred_at_utc, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                102,
                "guid-ada-102",
                handle_id,
                "Ada confirme le vol",
                "2026-01-16T10:00:00Z",
                "2026-01-16T10:00:00Z",
            ),
        )

    async def _chat_fail(**_kwargs):
        raise RuntimeError("LLM indisponible")

    monkeypatch.setattr("llm.chat", _chat_fail)

    second = asyncio.run(build_chapter(int(person_id), "2026-01"))
    assert second.get("deferred") is True
    assert second["status"] == "complete"
    assert second["narrative"] == good_narrative


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


def test_people_history_http_routes(history_db: Path) -> None:
    from fastapi.testclient import TestClient

    from database import get_db
    from database.person_history import upsert_chapter
    from main import app
    from tests.conftest import authenticate

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

    with TestClient(app) as client:
        authenticate(client)
        missing = client.get("/api/people/Inconnue/history")
        assert missing.status_code == 404
        listing = client.get("/api/people/Ada/history")
        assert listing.status_code == 200
        body = listing.json()
        assert body["person"] == "Ada"
        assert body["chapters"]
        rebuild = client.post("/api/people/Ada/history/rebuild")
        assert rebuild.status_code == 202
        assert rebuild.json()["status"] == "queued"


def _upsert_month(person_id: int, year_month: str, **overrides: object) -> None:
    from database.person_history import upsert_chapter

    payload = {
        "person_id": int(person_id),
        "year_month": year_month,
        "status": "complete",
        "message_count": 1,
        "sent_count": 0,
        "recv_count": 1,
        "highlights": [],
        "narrative": f"Récit {year_month}.",
        "mood_arc": "calme",
        "source_rowid_min": 1,
        "source_rowid_max": 1,
        "content_hash": year_month,
        "period_start_utc": f"{year_month}-01T00:00:00Z",
        "period_end_utc": f"{year_month}-28T00:00:00Z",
    }
    payload.update(overrides)
    upsert_chapter(**payload)  # type: ignore[arg-type]


def test_daily_and_person_targets_rebuild_existing_closed_and_current(
    history_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from database import get_db
    from scripts.person_history import _select_targets

    monkeypatch.setattr(
        "scripts.person_history.local_datetime",
        lambda: datetime(2026, 8, 15, 12, 0, tzinfo=ZoneInfo("Europe/Paris")),
    )
    with get_db() as conn:
        person_id = _seed_ada_with_noise(conn)
    _upsert_month(int(person_id), "2026-07")
    _upsert_month(int(person_id), "2026-08")

    daily = _select_targets({})
    person = _select_targets({"person_id": int(person_id)})
    assert (int(person_id), "2026-07") in daily
    assert (int(person_id), "2026-08") in daily
    assert (int(person_id), "2026-07") in person
    assert (int(person_id), "2026-08") in person


def test_highlight_quote_must_be_substring_of_source_message(
    history_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from database import get_db
    from scripts.person_history import build_chapter

    async def _chat(**_kwargs):
        return {
            "content": json.dumps(
                {
                    "highlights": [
                        {
                            "apple_rowid": 101,
                            "occurred_at_utc": "2026-01-15T10:00:00Z",
                            "quote": "inventé de toutes pièces",
                            "kind": "plan",
                        },
                        {
                            "apple_rowid": 101,
                            "occurred_at_utc": "2026-01-15T10:00:00Z",
                            "quote": "Ada prend l'avion",
                            "kind": "plan",
                        },
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
    result = asyncio.run(build_chapter(int(person_id), "2026-01"))
    quotes = [item["quote"] for item in result["highlights"]]
    assert "Ada prend l'avion" in quotes
    assert "inventé de toutes pièces" not in quotes


def test_history_digest_keeps_recent_months_and_highlights(history_db: Path) -> None:
    from database import get_db
    from database.person_history import digest_for_history

    with get_db() as conn:
        person_id = conn.execute("INSERT INTO people(name) VALUES ('Ada')").lastrowid
    long_text = "x" * 3000
    for month in ("2020-01", "2020-02", "2020-03", "2026-07", "2026-08"):
        highlights = []
        if month == "2026-08":
            highlights = [
                {
                    "apple_rowid": 101,
                    "occurred_at_utc": "2026-08-02T10:00:00Z",
                    "quote": "on se voit mardi",
                    "kind": "plan",
                }
            ]
        _upsert_month(
            int(person_id),
            month,
            narrative=f"{month} {long_text}",
            highlights=highlights,
        )
    digest = digest_for_history(int(person_id))
    assert "2026-08" in digest
    assert "2026-07" in digest
    assert "on se voit mardi" in digest
    assert "2020-01" not in digest


def test_missing_year_months_uses_local_month_not_utc(
    history_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from database import get_db
    from scripts.person_history import missing_year_months

    monkeypatch.setattr("config.TIMEZONE", "Europe/Paris")
    with get_db() as conn:
        person_id = conn.execute(
            "INSERT INTO people(name) VALUES ('Ada')"
        ).lastrowid
        handle_id = conn.execute(
            "INSERT INTO imessage_handles(apple_handle_id, handle) VALUES (?, ?)",
            (1, "+33600000001"),
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
                1,
                "guid-dst",
                handle_id,
                "bonne année",
                "2026-01-31T23:30:00Z",
                "2026-01-31T23:30:00Z",
            ),
        )
    assert missing_year_months(int(person_id)) == ["2026-02"]


def test_missing_year_months_does_not_loop_every_timestamp() -> None:
    import inspect

    from scripts.person_history import missing_year_months

    assert "for row in rows" not in inspect.getsource(missing_year_months)


def test_sync_imessage_counts_uses_mirror_not_extractor_cache(
    history_db: Path,
) -> None:
    from database import get_db
    from database.people import get_person, sync_imessage_counts_to_people

    with get_db() as conn:
        _seed_ada_with_noise(conn)
        conn.execute(
            """
            INSERT INTO imessage_messages(
                apple_rowid, guid, handle_id, text, occurred_at_utc, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                102,
                "guid-ada-102",
                conn.execute(
                    "SELECT id FROM imessage_handles WHERE handle = ?",
                    ("+33600000001",),
                ).fetchone()["id"],
                "deuxième",
                "2026-01-16T10:00:00Z",
                "2026-01-16T10:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO imessage_analysis_cache(
                handle, last_analyzed_rowid, total_messages_analyzed
            ) VALUES (?, 0, 0)
            """,
            ("+33600000001",),
        )
    sync_imessage_counts_to_people()
    person = get_person("Ada")
    assert person is not None
    assert int(person["imessage_count"] or 0) == 2


def test_merge_people_preserves_conflicting_month_chapters(history_db: Path) -> None:
    from database import get_db
    from database.people import _merge_people_ids
    from database.person_history import get_chapter

    with get_db() as conn:
        keep_id = conn.execute(
            "INSERT INTO people(name) VALUES ('Ada')"
        ).lastrowid
        drop_id = conn.execute(
            "INSERT INTO people(name) VALUES ('+33600000001')"
        ).lastrowid
        conn.execute(
            """
            INSERT INTO person_month_chapters (
                person_id, year_month, period_start_utc, period_end_utc, status,
                message_count, sent_count, recv_count, highlights_json, narrative,
                mood_arc, content_hash
            ) VALUES (?, '2026-08', '2026-08-01T00:00:00Z', '2026-08-31T23:59:59Z',
                      'complete', 10, 6, 4, '[]', 'Chapitre conservé.', '', 'keep-hash')
            """,
            (keep_id,),
        )
        conn.execute(
            """
            INSERT INTO person_month_chapters (
                person_id, year_month, period_start_utc, period_end_utc, status,
                message_count, sent_count, recv_count, highlights_json, narrative,
                mood_arc, content_hash
            ) VALUES (?, '2026-08', '2026-08-01T00:00:00Z', '2026-08-31T23:59:59Z',
                      'partial', 5, 2, 3, '[]', 'Chapitre fusionné.', '', 'drop-hash')
            """,
            (drop_id,),
        )
        _merge_people_ids(conn, int(keep_id), int(drop_id))
        remaining = conn.execute(
            "SELECT COUNT(*) AS c FROM person_month_chapters WHERE person_id = ?",
            (drop_id,),
        ).fetchone()["c"]
        assert int(remaining) == 0

    chapter = get_chapter(int(keep_id), "2026-08")
    assert chapter is not None
    assert chapter["message_count"] == 15
    assert "Chapitre conservé." in chapter["narrative"]
    assert "Chapitre fusionné." in chapter["narrative"]


def test_sync_contacts_merge_preserves_month_chapters(history_db: Path) -> None:
    from database import get_db
    from database.person_history import get_chapter
    from scripts.sync_contacts import _merge_into_existing

    with get_db() as conn:
        keep_id = conn.execute(
            "INSERT INTO people(name) VALUES ('Marie Martin')"
        ).lastrowid
        drop_id = conn.execute(
            "INSERT INTO people(name) VALUES ('+33612345678')"
        ).lastrowid
        conn.execute(
            """
            INSERT INTO person_month_chapters (
                person_id, year_month, period_start_utc, period_end_utc, status,
                message_count, sent_count, recv_count, highlights_json, narrative,
                mood_arc, content_hash
            ) VALUES (?, '2026-07', '2026-07-01T00:00:00Z', '2026-07-31T23:59:59Z',
                      'complete', 3, 1, 2, '[]', 'Chapitre juillet.', '', 'july-hash')
            """,
            (drop_id,),
        )
        _merge_into_existing(conn, int(keep_id), int(drop_id))

    chapter = get_chapter(int(keep_id), "2026-07")
    assert chapter is not None
    assert chapter["message_count"] == 3
    assert "Chapitre juillet." in chapter["narrative"]

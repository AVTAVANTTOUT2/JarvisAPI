"""La donnée persistée (contacts, iMessage) doit arriver telle quelle à JARVIS.

Vérifie le contrat validé : plus de jetons [PERSON_n] / [PHONE_n] / [EMAIL_n]
sur la sortie LLM ; les secrets restent masqués.
"""

from __future__ import annotations

import json
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


PERSON_NAME = "Camille Rivière"
PERSON_PHONE = "+33699887766"
PERSON_EMAIL = "camille.riviere@example.test"
MESSAGE_TEXT = "RDV pizza à Wazemmes demain 19h30"
SECRET = "sk-passThroughSecret123456789"


@pytest.fixture()
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import config
    import database

    db_path = tmp_path / "pass-through-pii.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    return db_path


def _mark_live_sources_complete() -> None:
    from database.ingestion import bind_connector, update_ingestion_source_state

    refreshed_at = datetime.now(timezone.utc).isoformat()
    for source in ("mail", "calendar", "imessage"):
        bind_connector(source, permission_state="granted")
        update_ingestion_source_state(
            source,
            status="idle",
            completeness="complete",
            cursor={"full_history": True},
            coverage_start_utc="1970-01-01T00:00:00Z",
            coverage_end_utc="2100-01-01T00:00:00Z",
            last_success_at=refreshed_at,
        )


def _seed_contact_and_message() -> dict[str, str]:
    from database import get_db

    with get_db() as conn:
        person_id = conn.execute(
            """
            INSERT INTO people(name, relationship, personality_notes)
            VALUES (?, ?, ?)
            """,
            (PERSON_NAME, "amie", f"{PERSON_PHONE} {PERSON_EMAIL}"),
        ).lastrowid
        handle_id = conn.execute(
            "INSERT INTO imessage_handles(apple_handle_id, handle) VALUES (?, ?)",
            (42, PERSON_PHONE),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO imessage_messages(
                apple_rowid, guid, handle_id, text, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                42,
                "imessage-pass-through-pii",
                handle_id,
                MESSAGE_TEXT,
                "2026-08-18T18:00:00Z",
            ),
        )
        row = conn.execute(
            """
            SELECT p.name, p.personality_notes, h.handle, m.text
            FROM people p, imessage_handles h, imessage_messages m
            WHERE p.id = ? AND h.id = ? AND m.guid = ?
            """,
            (person_id, handle_id, "imessage-pass-through-pii"),
        ).fetchone()
    return {
        "name": str(row["name"]),
        "notes": str(row["personality_notes"]),
        "handle": str(row["handle"]),
        "text": str(row["text"]),
    }


def _decode_retrieval_context(block: str) -> dict:
    assert block.startswith("[UNTRUSTED_DATA:KNOWLEDGE_RETRIEVAL]")
    envelope = json.loads(block.splitlines()[1])
    return json.loads(envelope["content"])


def test_redact_for_external_llm_keeps_pii_and_strips_secrets() -> None:
    from jarvis.security.llm_data_boundary import redact_for_external_llm

    mixed = (
        f"{PERSON_NAME} ({PERSON_PHONE}, {PERSON_EMAIL}) "
        f"écrit : {MESSAGE_TEXT}. Token {SECRET}"
    )
    safe = redact_for_external_llm(mixed, max_chars=2_000)
    assert PERSON_NAME in safe
    assert PERSON_PHONE in safe
    assert PERSON_EMAIL in safe
    assert MESSAGE_TEXT in safe
    assert SECRET not in safe
    assert "[PERSON_" not in safe
    assert "[PHONE_" not in safe
    assert "[EMAIL_" not in safe


def test_database_row_matches_retrieval_context_sent_to_jarvis(isolated_db: Path) -> None:
    from jarvis.retrieval import RetrievalRequest, format_retrieval_context, search_knowledge

    stored = _seed_contact_and_message()
    _mark_live_sources_complete()

    result = search_knowledge(
        RetrievalRequest(
            query=f"{PERSON_NAME} {MESSAGE_TEXT}",
            source_types=("person", "imessage"),
            max_hits=8,
        )
    )
    formatted = format_retrieval_context(result, max_chars=8_000)
    payload = _decode_retrieval_context(formatted)

    assert stored["name"] == PERSON_NAME
    assert stored["handle"] == PERSON_PHONE
    assert stored["text"] == MESSAGE_TEXT
    assert PERSON_NAME in formatted
    assert PERSON_PHONE in formatted
    assert PERSON_EMAIL in formatted
    assert MESSAGE_TEXT in formatted
    assert "[PERSON_" not in formatted
    assert "[PHONE_" not in formatted
    serialized_hits = json.dumps(payload["hits"], ensure_ascii=False)
    assert PERSON_NAME in serialized_hits or MESSAGE_TEXT in serialized_hits


@pytest.mark.asyncio
async def test_jarvis_reply_quotes_the_same_database_row(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agents
    from agents.info import InfoAgent
    from jarvis.retrieval import RetrievalRequest, format_retrieval_context, search_knowledge

    stored = _seed_contact_and_message()
    _mark_live_sources_complete()
    result = search_knowledge(
        RetrievalRequest(
            query=f"{PERSON_NAME} pizza",
            source_types=("person", "imessage"),
            max_hits=8,
        )
    )
    formatted = format_retrieval_context(result, max_chars=8_000)

    jarvis_reply = (
        f"[neutral]\n{stored['name']} ({stored['handle']}) a écrit : {stored['text']}"
    )
    fake = AsyncMock(
        return_value={
            "content": jarvis_reply,
            "model": "fake-llm",
            "tokens_in": 1,
            "tokens_out": 1,
            "cost": 0.0,
        }
    )
    monkeypatch.setattr(agents.llm, "chat", fake)
    monkeypatch.setattr(
        agents,
        "event_bus",
        types.SimpleNamespace(emit=AsyncMock(return_value=None)),
    )

    outcome = await InfoAgent()._call_claude(
        f"Que dit {PERSON_NAME} ?",
        context={
            "retrieval_context": formatted,
            "__defer_persist": True,
        },
        persist=False,
    )

    sent = json.dumps(fake.call_args.kwargs, ensure_ascii=False)
    assert stored["name"] in sent
    assert stored["handle"] in sent
    assert stored["text"] in sent
    assert "[PERSON_" not in sent
    assert "[PHONE_" not in sent
    assert stored["name"] in outcome["response"]
    assert stored["handle"] in outcome["response"]
    assert stored["text"] in outcome["response"]


@pytest.mark.asyncio
async def test_message_intelligence_sends_handle_and_body_verbatim(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis import message_intelligence

    stored = _seed_contact_and_message()
    captured: dict[str, str] = {}

    async def fake_generate(prompt: str, system=None, **_kwargs):
        captured["prompt"] = prompt
        return (
            '{"announcements":["'
            + stored["name"]
            + " : "
            + stored["text"]
            + '"],"tasks":[],"suggestions":[]}'
        )

    class FakeRouter:
        deepseek = types.SimpleNamespace(generate=fake_generate)

    monkeypatch.setattr(message_intelligence, "_ensure_components", lambda: FakeRouter())

    result = await message_intelligence.analyze_message_batch(
        [
            {
                "handle": stored["handle"],
                "text": stored["text"],
                "is_from_me": 0,
            }
        ],
        since_id=42,
        source="imessage",
    )

    assert stored["handle"] in captured["prompt"]
    assert stored["text"] in captured["prompt"]
    assert "[PERSON_" not in captured["prompt"]
    assert result["status"] == "ok"
    assert stored["text"] in result["result"]

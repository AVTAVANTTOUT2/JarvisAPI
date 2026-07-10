"""Tests : journal parallèle de JARVIS (généré depuis des faits réels, LLM mocké)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def test_day_facts_gathers_messages_tasks_visits(tmp_db):
    from database import create_task, get_db, update_task_status
    from scripts.jarvis_journal import _day_facts, _today

    with get_db() as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (NULL, 'user', 'salut')"
        )
    task_id = create_task("Faire les courses")
    update_task_status(task_id, "done")

    facts = _day_facts(_today())
    assert facts["messages"] == 1
    assert facts["tasks_done"] == ["Faire les courses"]


def test_day_facts_empty_day_returns_empty_lists(tmp_db):
    from scripts.jarvis_journal import _day_facts

    facts = _day_facts("2020-01-01")
    assert facts["messages"] == 0
    assert facts["tasks_done"] == []
    assert facts["visits"] == []
    assert facts["mood"] is None


@pytest.mark.asyncio
async def test_generate_journal_entry_persists_and_returns_entry(tmp_db):
    from database import get_jarvis_journal_entry
    from scripts.jarvis_journal import generate_journal_entry

    fake_result = {"content": "Monsieur a eu une journée sans histoire.", "tokens_in": 10,
                    "tokens_out": 5, "cache_hit": 0, "cost": 0.0, "model": "test", "stop_reason": "stop"}
    with patch("llm.chat", new=AsyncMock(return_value=fake_result)):
        out = await generate_journal_entry(date="2026-07-10")

    assert out["entry"] == "Monsieur a eu une journée sans histoire."
    stored = get_jarvis_journal_entry("2026-07-10")
    assert stored["entry"] == "Monsieur a eu une journée sans histoire."


@pytest.mark.asyncio
async def test_generate_journal_entry_falls_back_when_llm_fails(tmp_db):
    from scripts.jarvis_journal import generate_journal_entry

    with patch("llm.chat", new=AsyncMock(side_effect=RuntimeError("API down"))):
        out = await generate_journal_entry(date="2026-07-10")

    assert out["entry"]  # fallback non vide
    assert "2026-07-10" in out["entry"]


@pytest.mark.asyncio
async def test_generate_journal_entry_never_raises_on_llm_error(tmp_db):
    from scripts.jarvis_journal import generate_journal_entry

    with patch("llm.chat", new=AsyncMock(side_effect=Exception("boom"))):
        out = await generate_journal_entry(date="2026-07-10")
    assert out is not None

"""Moteur de briefings — priorisation, dédup, version vocale, delta, résilience."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.briefing_engine import (  # noqa: E402
    BriefingItem,
    _dedupe,
    _prio_rank,
    generate_structured_briefing,
)


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "briefing.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def _item(id_: str, title: str, priority: str, source: str, dedupe_key: str = "") -> BriefingItem:
    return BriefingItem(
        id=id_, title=title, detail="d", priority=priority,  # type: ignore[arg-type]
        source=source, freshness="db", dedupe_key=dedupe_key,
    )


# ── Dédup + priorisation (pur, sans LLM) ────────────────────


def test_dedupe_removes_same_key():
    items = [
        _item("a", "Payer loyer", "critique", "email", "email:1"),
        _item("b", "Payer loyer", "aujourd_hui", "tasks", "email:1"),
        _item("c", "Autre", "information", "weather"),
    ]
    out = _dedupe(items)
    assert len(out) == 2
    assert out[0].id == "a"  # premier gagne


def test_dedupe_fallback_key_source_title():
    items = [
        _item("a", "Réunion 10h", "aujourd_hui", "calendar"),
        _item("b", "Réunion 10h", "aujourd_hui", "calendar"),
    ]
    assert len(_dedupe(items)) == 1


def test_priority_ranking_order():
    assert _prio_rank("critique") < _prio_rank("aujourd_hui")
    assert _prio_rank("aujourd_hui") < _prio_rank("surveiller")
    assert _prio_rank("surveiller") < _prio_rank("information")


# ── Génération complète (LLM mocké, sources mockées) ────────


def _fake_sources(items, unavailable=None):
    async def _collect():
        return items, unavailable or [], {}

    return _collect


def _llm_mock():
    async def _chat(messages, model="", system="", **kwargs):
        prompt = messages[0]["content"] if messages else ""
        if "vocale" in prompt:
            return {"content": "Bonjour Monsieur. Trois points ce matin.", "cost": 0.0}
        return {"content": "BRIEFING COMPLET GÉNÉRÉ", "cost": 0.0}

    return AsyncMock(side_effect=_chat)


def test_generate_morning_full_and_voice(tmp_db):
    items = [
        _item("t1", "Tâche urgente", "critique", "tasks", "task:1"),
        _item("c1", "RDV 10h", "aujourd_hui", "calendar", "cal:1"),
    ]
    with patch("agents.briefing_engine.collect_briefing_sources", new=_fake_sources(items)), \
         patch("llm.chat", new=_llm_mock()):
        briefing = asyncio.run(generate_structured_briefing("morning"))

    assert briefing.kind == "morning"
    assert briefing.full_text == "BRIEFING COMPLET GÉNÉRÉ"
    assert "Bonjour Monsieur" in briefing.voice_text
    assert len(briefing.items) == 2
    # Version vocale courte : jamais un dump
    assert len(briefing.voice_text) < 500


def test_generate_voice_only_skips_main_model(tmp_db):
    """voice_only → un seul appel LLM (Flash), pas de rédaction écran Main."""
    calls = []

    async def _chat(messages, model="", system="", **kwargs):
        calls.append(model)
        return {"content": "Version vocale.", "cost": 0.0}

    items = [_item("t1", "Tâche", "aujourd_hui", "tasks", "task:1")]
    with patch("agents.briefing_engine.collect_briefing_sources", new=_fake_sources(items)), \
         patch("llm.chat", new=AsyncMock(side_effect=_chat)):
        briefing = asyncio.run(generate_structured_briefing("morning", voice_only=True))

    assert len(calls) == 1  # uniquement la version vocale
    assert briefing.voice_text == "Version vocale."


def test_generate_filter_priority(tmp_db):
    items = [
        _item("t1", "Critique", "critique", "tasks", "task:1"),
        _item("t2", "Info", "information", "weather", "w:1"),
    ]
    with patch("agents.briefing_engine.collect_briefing_sources", new=_fake_sources(items)), \
         patch("llm.chat", new=_llm_mock()):
        briefing = asyncio.run(
            generate_structured_briefing("morning", filter_priority="critique")
        )
    assert [i.priority for i in briefing.items] == ["critique"]


def test_generate_work_only_filters_sources(tmp_db):
    items = [
        _item("t1", "Tâche", "aujourd_hui", "tasks", "task:1"),
        _item("w1", "Météo", "information", "weather", "w:1"),
    ]
    with patch("agents.briefing_engine.collect_briefing_sources", new=_fake_sources(items)), \
         patch("llm.chat", new=_llm_mock()):
        briefing = asyncio.run(generate_structured_briefing("morning", work_only=True))
    assert {i.source for i in briefing.items} == {"tasks"}


def test_generate_no_data_graceful(tmp_db):
    with patch(
        "agents.briefing_engine.collect_briefing_sources",
        new=_fake_sources([], [{"source": "calendar", "reason": "indisponible"}]),
    ), patch("llm.chat", new=_llm_mock()):
        briefing = asyncio.run(generate_structured_briefing("morning"))
    assert briefing.items == []
    assert any(u["source"] == "calendar" for u in briefing.unavailable)


def test_generate_llm_down_falls_back_to_structure(tmp_db):
    """DeepSeek indisponible → structure brute, jamais de crash ni fausse réponse."""
    items = [_item("t1", "Tâche", "aujourd_hui", "tasks", "task:1")]

    async def _chat_down(*a, **k):
        raise RuntimeError("DeepSeek down")

    with patch("agents.briefing_engine.collect_briefing_sources", new=_fake_sources(items)), \
         patch("llm.chat", new=AsyncMock(side_effect=_chat_down)):
        briefing = asyncio.run(generate_structured_briefing("morning"))

    assert "Tâche" in briefing.full_text  # structure brute conservée
    assert any(u["source"] == "deepseek_main" for u in briefing.unavailable)


def test_delta_returns_only_new_items(tmp_db):
    """« Qu'est-ce qui a changé depuis ce matin ? » n'inclut pas le déjà-vu."""
    morning_items = [
        _item("t1", "Tâche A", "aujourd_hui", "tasks", "task:1"),
    ]
    with patch("agents.briefing_engine.collect_briefing_sources", new=_fake_sources(morning_items)), \
         patch("llm.chat", new=_llm_mock()):
        asyncio.run(generate_structured_briefing("morning"))

    later_items = [
        _item("t1", "Tâche A", "aujourd_hui", "tasks", "task:1"),  # déjà vue
        _item("t2", "Tâche B nouvelle", "aujourd_hui", "tasks", "task:2"),
    ]
    with patch("agents.briefing_engine.collect_briefing_sources", new=_fake_sources(later_items)), \
         patch("llm.chat", new=_llm_mock()):
        delta = asyncio.run(generate_structured_briefing("delta"))

    ids = [i.id for i in delta.items]
    assert "t2" in ids and "t1" not in ids


def test_evening_kind_uses_evening_prompt(tmp_db):
    prompts: list[str] = []

    async def _chat(messages, model="", system="", **kwargs):
        prompts.append(messages[0]["content"])
        return {"content": "Bilan du soir.", "cost": 0.0}

    items = [_item("t1", "Tâche", "aujourd_hui", "tasks", "task:1")]
    with patch("agents.briefing_engine.collect_briefing_sources", new=_fake_sources(items)), \
         patch("llm.chat", new=AsyncMock(side_effect=_chat)):
        briefing = asyncio.run(generate_structured_briefing("evening"))

    assert briefing.kind == "evening"
    assert any("terminé / reporté / bloqué" in p for p in prompts)


def test_items_carry_source_and_freshness():
    item = _item("x", "Titre", "critique", "email", "email:1")
    d = item.to_dict()
    assert d["source"] == "email"
    assert d["freshness"] == "db"
    assert d["priority"] == "critique"

"""Contrats transversaux du snapshot knowledge conversationnel."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

import config
import database
from jarvis.agentic.turn_context import (
    AGENTIC_ROUTING_METADATA_KEY,
    SNAPSHOT_METADATA_KEY,
    TurnKnowledgeSnapshot,
)
from jarvis.cognitive.models import TaskIntent
from jarvis.event_bus import EventBus
from jarvis.task_control.detection import TaskCandidateDetector
from jarvis.task_control.models import PlanDecision, PlanStep, TaskPlan, new_id
from jarvis.task_control.service import TaskControlService


def _snapshot(query: str = "Retrouve le mail de Grégoire") -> TurnKnowledgeSnapshot:
    return TurnKnowledgeSnapshot.capture(
        query=query,
        conversation_id=42,
        interaction_mode="chat",
        context={
            "retrieval_context": (
                "[UNTRUSTED_DATA:KNOWLEDGE_RETRIEVAL]\n"
                "Mail de Grégoire — reçu hier — projet Atlas\n"
                "[/UNTRUSTED_DATA:KNOWLEDGE_RETRIEVAL]"
            ),
            "__retrieval": {
                "status": "ok",
                "verified_sources": ["email"],
                "partial_sources": [],
                "unavailable_sources": [],
                "latency_ms": 12.0,
            },
            "__retrieval_references": [
                {"uid": "email:gregoire", "source_type": "email", "source_id": "7"}
            ],
            "__retrieval_done": True,
            "history": [{"role": "user", "content": "Il m'a écrit hier"}],
        },
    )


@dataclass
class _FakeRun:
    run_id: str
    status: Any = None


@dataclass
class _FakeAgenticService:
    starts: list[dict[str, Any]] = field(default_factory=list)

    async def create_and_start(self, **kwargs: Any) -> _FakeRun:
        self.starts.append(kwargs)
        return _FakeRun(run_id=f"run_{len(self.starts)}")


@dataclass
class _FakeNotifications:
    def create(self, **_kwargs: Any) -> int:
        return 1


async def _planner(task, *, version: int, context=None) -> TaskPlan:
    assert context is not None
    assert "KNOWLEDGE_RETRIEVAL" in context["retrieval_context"]
    return TaskPlan(
        plan_id=new_id("plan"),
        task_id=task.task_id,
        version=version,
        objective="Retrouver et résumer le mail de Grégoire",
        summary="Plan de recherche",
        steps=(PlanStep(index=1, title="Lire le mail"),),
        expected_deliverables=("Résumé",),
    )


@pytest.fixture
def task_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "turn-knowledge.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    return db_path


@pytest.mark.asyncio
async def test_plan_approval_keeps_snapshot_classification_and_read_scopes(
    task_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import agentic_processing, chat_context
    from jarvis.task_control import service as service_module

    runtime = _FakeAgenticService()
    control = TaskControlService(
        agentic_service=runtime,
        notifications=_FakeNotifications(),
        bus=EventBus(),
        planner=_planner,
        detector=TaskCandidateDetector(),
    )
    monkeypatch.setattr(service_module, "_service", control, raising=False)
    monkeypatch.setattr(config, "AGENTIC_REQUIRE_PLAN_APPROVAL", True)
    monkeypatch.setattr(agentic_processing, "save_message", lambda *a, **k: None)
    prepared = _snapshot()
    prepare_calls = 0

    async def prepare(*_args, **_kwargs):
        nonlocal prepare_calls
        prepare_calls += 1
        return prepared

    monkeypatch.setattr(chat_context, "prepare_turn", prepare)

    response = await agentic_processing.maybe_start_agentic_run(
        "/agent retrouve le mail de Grégoire dont je t'ai parlé et résume-le",
        42,
        channel="chat",
        voice_mode=False,
        persist_assistant=False,
    )

    assert response is not None
    assert prepare_calls == 1
    task = control.repository.require_task(response["task_control"]["task_id"])
    assert task.metadata[SNAPSHOT_METADATA_KEY]["snapshot_id"] == prepared.snapshot_id
    assert (
        task.metadata[AGENTIC_ROUTING_METADATA_KEY]["capability_profile_id"]
        == "readonly-research"
    )

    await control.decide_plan(
        task.task_id,
        1,
        decision=PlanDecision.APPROVED,
        actor="session:test",
    )

    assert len(runtime.starts) == 1
    launch = runtime.starts[0]
    assert launch["capability_profile_id"] == "readonly-research"
    assert "communications:read" in launch["permissions"]
    assert launch["category"].value == "agentic_readonly"
    assert launch["selected_context"]["turn_snapshot_id"] == prepared.snapshot_id
    assert "Grégoire" in launch["selected_context"]["retrieval_context"]
    assert launch["selected_context"]["retrieval_references"][0]["uid"] == (
        "email:gregoire"
    )


@pytest.mark.asyncio
async def test_action_followup_reuses_the_same_prepared_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import chat_processing

    context = _snapshot("Quel temps fait-il ?").to_context()
    build = AsyncMock(return_value=context)
    start_agentic = AsyncMock(return_value=None)
    legacy = AsyncMock(return_value=None)
    handle = AsyncMock(
        side_effect=[
            {
                "response": '```action {"type":"weather"}```',
                "agent": "info",
                "model": "flash",
                "tokens_in": 1,
                "tokens_out": 1,
                "cost": 0.0,
                "emotion": "neutral",
            },
            {
                "response": "Il fait beau.",
                "agent": "info",
                "model": "flash",
                "tokens_in": 1,
                "tokens_out": 1,
                "cost": 0.0,
                "emotion": "neutral",
            },
        ]
    )
    execute = AsyncMock(return_value={"ok": True, "temp": 20})
    monkeypatch.setattr(chat_processing, "_build_enriched_context", build)
    monkeypatch.setattr(chat_processing, "maybe_start_agentic_run", start_agentic)
    monkeypatch.setattr(chat_processing, "maybe_handle_legacy_agentic_chat", legacy)
    monkeypatch.setattr(chat_processing.orchestrator, "handle", handle)
    monkeypatch.setattr(chat_processing, "execute_action", execute)
    monkeypatch.setattr(chat_processing, "save_message", lambda *a, **k: None)
    monkeypatch.setattr(
        chat_processing, "update_conversation_activity", lambda *a, **k: None
    )
    monkeypatch.setattr(
        chat_processing, "schedule_conversation_title", lambda *a, **k: None
    )

    result = await chat_processing._process_message_internal(
        "Quel temps fait-il ?",
        42,
    )

    build.assert_awaited_once()
    assert handle.await_count == 2
    assert handle.await_args_list[0].kwargs["context"] is context
    assert handle.await_args_list[1].kwargs["context"] is context
    assert execute.await_args.kwargs["knowledge_context"] is context
    assert result["knowledge"]["snapshot_id"] == context["__turn_snapshot_id"]


@pytest.mark.asyncio
async def test_voice_heavy_answer_receives_history_and_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import chat_context, voice_cognitive
    from jarvis import notification_service as notification_module

    snapshot = _snapshot("Analyse ce que Grégoire m'a envoyé")
    intent = TaskIntent(
        interaction_mode="voice",
        domain="general",
        complexity="heavy",
        execution_type="answer",
        reasoning_model="main",
        prompt_model="main",
        voice_ack="Je prépare l'analyse.",
    )
    monkeypatch.setattr(voice_cognitive, "route_request", lambda *_a, **_k: intent)
    monkeypatch.setattr(chat_context, "prepare_turn", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(voice_cognitive, "_save_voice_messages", lambda *a, **k: None)
    monkeypatch.setattr(voice_cognitive, "_save_voice_debug_trace", lambda *_a: 1)

    async def no_broadcast(_trace):
        return None

    monkeypatch.setattr(voice_cognitive, "_broadcast_voice_debug", no_broadcast)
    monkeypatch.setattr(database, "save_message", lambda *a, **k: None)
    monkeypatch.setattr(
        notification_module.notification_service,
        "create",
        lambda **_kwargs: 1,
    )
    calls: list[dict[str, Any]] = []
    completed = __import__("asyncio").Event()

    async def fake_chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"content": "Analyse complète", "model": "main"}
        completed.set()
        return {"content": "Résumé vocal", "model": "flash"}

    monkeypatch.setattr(voice_cognitive.llm, "chat", fake_chat)

    response = await voice_cognitive.maybe_handle_cognitive_voice(
        "Analyse ce que Grégoire m'a envoyé",
        42,
        t0=0.0,
    )
    await __import__("asyncio").wait_for(completed.wait(), timeout=1.0)

    assert response is not None
    assert response["knowledge"]["snapshot_id"] == snapshot.snapshot_id
    assert "KNOWLEDGE_RETRIEVAL" in calls[0]["system"]
    assert "Il m'a écrit hier" in calls[0]["messages"][0]["content"]
    assert "UNTRUSTED_DATA:HISTORY_USER" in calls[0]["messages"][0]["content"]
    assert calls[0]["messages"][-1]["content"].startswith("Analyse")


@pytest.mark.asyncio
async def test_raw_agentic_api_assigns_profile_scopes_and_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import chat_context, router_agentic

    snapshot = _snapshot()
    monkeypatch.setattr(chat_context, "prepare_turn", AsyncMock(return_value=snapshot))
    captured: dict[str, Any] = {}

    class Service:
        async def create_and_start(self, **kwargs):
            captured.update(kwargs)
            return _FakeRun(run_id="run_raw")

    monkeypatch.setattr(router_agentic, "get_agentic_service", lambda: Service())
    response = await router_agentic.create_agentic_run(
        router_agentic.CreateRunRequest(
            title="Retrouver le mail de Grégoire",
            request="Retrouve le mail de Grégoire et résume-le",
        )
    )

    assert captured["capability_profile_id"] == "readonly-research"
    assert "communications:read" in captured["permissions"]
    assert captured["selected_context"]["turn_snapshot_id"] == snapshot.snapshot_id
    assert response["knowledge"]["references"][0]["uid"] == "email:gregoire"

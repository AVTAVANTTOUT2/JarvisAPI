"""Réponses fail-closed du planificateur agentique."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.agentic_planning import (
    _planning_unavailable_response,
    constraint_blocked_response,
    plan_instead_of_running,
)


def test_planning_unavailable_response_shape_without_snapshot() -> None:
    saved: list[tuple] = []

    def _save(*args, **kwargs):
        saved.append((args, kwargs))

    response = _planning_unavailable_response(
        conversation_id=42,
        persist_assistant=True,
        save_message_fn=_save,
        snapshot=None,
    )

    assert response["action_result"]["error"] == "planning_unavailable"
    assert response["action_result"]["accepted"] is False
    assert response["action_result"]["awaiting_plan_approval"] is False
    assert "knowledge" not in response
    assert saved and saved[0][0][0] == 42
    assert saved[0][1]["agent"] == "agentic"
    assert "Rien n'a été lancé" in response["text"]


def test_planning_unavailable_response_attaches_snapshot_payload() -> None:
    snapshot = SimpleNamespace(
        public_payload=lambda: {"hits": 1, "sources": ["tasks"]},
    )
    response = _planning_unavailable_response(
        conversation_id=7,
        persist_assistant=False,
        save_message_fn=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("ne doit pas persister")
        ),
        snapshot=snapshot,
    )
    assert response["knowledge"] == {"hits": 1, "sources": ["tasks"]}


def test_constraint_blocked_response_voice_shortens_and_exposes_routing() -> None:
    classification = SimpleNamespace(
        category=SimpleNamespace(value="coding"),
        reason="multi_step",
        blocked_category=SimpleNamespace(value="coding"),
        constraints=SimpleNamespace(
            evidence=["ne lance pas"],
            public_payload=lambda: {"signals": ["no_execution"]},
        ),
    )
    response = constraint_blocked_response(
        classification,
        conversation_id=1,
        voice_mode=True,
        persist_assistant=False,
        save_message_fn=lambda *_a, **_k: None,
    )
    assert response["action_result"]["reason"] == "execution_constraint"
    assert response["routing"]["blocked_category"] == "coding"
    assert "ne lance pas" not in response["text"]


@pytest.mark.asyncio
async def test_plan_instead_of_running_fail_closed_when_ingest_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*_a, **_k):
        raise RuntimeError("planner down")

    monkeypatch.setattr(
        "jarvis.task_control.ingest.create_task_from_user_request",
        _boom,
    )
    response = await plan_instead_of_running(
        "Corrige les tests",
        conversation_id=9,
        channel="web",
        voice_mode=False,
        persist_assistant=False,
        save_message_fn=lambda *_a, **_k: None,
    )
    assert response["action_result"]["error"] == "planning_unavailable"
    assert response["action"] is None

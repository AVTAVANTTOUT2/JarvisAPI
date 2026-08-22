"""Demandes ouvertes du type « book me a hotel » — plan, pas crash, pas paiement."""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.agentic.classifier import (
    classify_agentic_request,
    is_open_world_booking_request,
)
from jarvis.agentic.models import AgenticRequestCategory
from jarvis.agentic.profiles import (
    CAPABILITY_PROFILES,
    select_capability_profile,
)
from jarvis.agentic.turn_context import AGENTIC_ROUTING_METADATA_KEY
from jarvis.task_control.models import ControlTask, new_id
from jarvis.task_control.planner import build_plan, fallback_plan_payload


HOTEL_EN = "book me a hotel in Barcelona"
HOTEL_FR = "réserve-moi un hôtel à Barcelone"


@pytest.mark.parametrize(
    "request_text",
    [HOTEL_EN, HOTEL_FR, "find me a hotel in Paris", "book a hotel in Barcelona"],
)
def test_hotel_request_is_open_world_booking(request_text: str) -> None:
    assert is_open_world_booking_request(request_text) is True
    assert (
        classify_agentic_request(request_text).category
        is AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT
    )


@pytest.mark.parametrize(
    "request_text",
    [
        "facebook",
        "open facebook",
        "notebook",
        "I loved that book",
        "explique ce fichier",
    ],
)
def test_book_substring_is_not_a_booking(request_text: str) -> None:
    assert is_open_world_booking_request(request_text) is False
    assert (
        classify_agentic_request(request_text).category
        is AgenticRequestCategory.DIRECT_ACTION
    )


@pytest.mark.parametrize("request_text", [HOTEL_EN, HOTEL_FR])
def test_hotel_request_selects_browser_without_payment_grant(
    request_text: str,
) -> None:
    profile = select_capability_profile(
        request_text, AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT
    )
    assert profile.profile_id == "browser"
    assert "research:search" in profile.default_permissions
    assert "browser:control" in profile.default_permissions
    assert "financial:act" not in profile.default_permissions
    assert "financial:act" not in profile.permissions
    assert "financial:act" in CAPABILITY_PROFILES["browser"].denied_permissions


def test_hotel_fallback_plan_searches_and_stops_before_payment() -> None:
    task = ControlTask(
        task_id=new_id("task"),
        profile_id="default",
        title=HOTEL_EN,
        description=HOTEL_EN,
    )
    plan = build_plan(task, fallback_plan_payload(task), version=1)
    tools = set(plan.tools_expected)
    assert "web_search" in tools
    assert "browser" in tools
    assert "read_file" not in tools
    assert "workspace:write" not in plan.permissions_expected
    assert "financial:act" in plan.permissions_expected
    assert any("financial:act" in risk for risk in plan.risks)
    blob = " ".join(
        [plan.summary, *(step.title + " " + step.detail for step in plan.steps)]
    ).lower()
    assert "paiement" in blob or "payer" in blob
    assert "read_file" not in blob


def test_code_fallback_plan_is_unchanged_for_non_booking_tasks() -> None:
    task = ControlTask(
        task_id=new_id("task"),
        profile_id="default",
        title="Corriger le rapport interne",
        description="Relire le dépôt et produire le rapport.",
    )
    payload = fallback_plan_payload(task)
    assert payload["steps"][0]["tools"] == ["read_file"]
    plan = build_plan(task, payload, version=1)
    assert "web_search" not in plan.tools_expected
    assert "browser" not in plan.tools_expected


@pytest.mark.asyncio
@pytest.mark.parametrize("request_text", [HOTEL_EN, HOTEL_FR])
async def test_hotel_request_starts_a_plan_not_a_run(
    monkeypatch: pytest.MonkeyPatch, request_text: str
) -> None:
    import config
    from api import agentic_processing
    from jarvis.agentic.turn_context import TurnKnowledgeSnapshot
    from jarvis.task_control import ingest as ingest_module

    captured: dict[str, Any] = {}

    async def fake_prepare(text, conversation_id, **_kwargs):
        return TurnKnowledgeSnapshot.capture(
            query=text,
            conversation_id=conversation_id,
            interaction_mode="chat",
            context={},
        )

    async def fake_create(request, **kwargs):
        captured["request"] = request
        captured["metadata"] = kwargs.get("metadata")
        return {
            "task_id": "task_hotel",
            "status": "awaiting_plan_approval",
            "spoken": "plan",
        }

    class _BoomRuntime:
        async def create_and_start(self, **_kwargs):
            raise AssertionError("une réservation ne doit pas démarrer un run")

        def list(self, **_kwargs):
            return []

        def get(self, _run_id):
            return None

    monkeypatch.setattr(config, "AGENTIC_REQUIRE_PLAN_APPROVAL", True)
    monkeypatch.setattr(config, "AGENTIC_DEFAULT_PROFILE", "readonly-research")
    monkeypatch.setattr(config, "AGENTIC_PROFILE_ROUTE_OVERRIDES", {})
    monkeypatch.setattr("api.chat_context.prepare_turn", fake_prepare)
    monkeypatch.setattr(ingest_module, "create_task_from_user_request", fake_create)
    monkeypatch.setattr(agentic_processing, "get_agentic_service", _BoomRuntime)
    monkeypatch.setattr(agentic_processing, "save_message", lambda *a, **k: None)

    response = await agentic_processing.maybe_start_agentic_run(
        request_text,
        42,
        channel="chat",
        voice_mode=False,
        persist_assistant=False,
    )

    assert response is not None
    assert response["action_result"]["ok"] is True
    assert response["action_result"]["awaiting_plan_approval"] is True
    assert response["action_result"].get("error") is None
    assert captured["request"] == request_text
    routing = captured["metadata"][AGENTIC_ROUTING_METADATA_KEY]
    assert routing["capability_profile_id"] == "browser"
    assert routing["category"] == AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT.value
    assert "financial:act" not in routing["permissions"]
    assert "browser:control" in routing["permissions"]
    assert "research:search" in routing["permissions"]

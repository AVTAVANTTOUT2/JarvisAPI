"""Convergence texte/voix des contrôles de runs agentiques."""

from __future__ import annotations

from dataclasses import replace

import pytest

from jarvis.agentic.models import (
    AgenticRequestCategory,
    AgenticRun,
    AgenticRunStatus,
    ApprovalDecision,
    ApprovalRequest,
)


APPROVAL_ID = "11111111-1111-4111-8111-111111111111"


class _Service:
    def __init__(
        self, runs: list[AgenticRun], approvals: list[ApprovalRequest] | None = None
    ):
        self.runs = {run.run_id: run for run in runs}
        self.approval_items = approvals or []
        self.decisions: list[tuple] = []
        self.created = 0

    def list(self, **_kwargs):
        return list(reversed(tuple(self.runs.values())))

    def get(self, run_id: str):
        return self.runs.get(run_id)

    def approvals(self, run_id: str):
        return [item for item in self.approval_items if item.run_id == run_id]

    async def pause(self, run_id: str):
        self.runs[run_id] = replace(
            self.runs[run_id], status=AgenticRunStatus.PAUSED, phase="paused"
        )
        return self.runs[run_id]

    async def resume(self, run_id: str):
        self.runs[run_id] = replace(
            self.runs[run_id], status=AgenticRunStatus.RUNNING, phase="running"
        )
        return self.runs[run_id]

    async def cancel(self, run_id: str):
        self.runs[run_id] = replace(
            self.runs[run_id], status=AgenticRunStatus.CANCELLED, phase="cancelled"
        )
        return self.runs[run_id]

    async def decide_approval(self, run_id, approval_id, decision, **kwargs):
        self.decisions.append((run_id, approval_id, decision, kwargs))
        return replace(
            self.approval_items[0],
            decision=ApprovalDecision(decision),
            decision_by=kwargs["decided_by"],
        )


def _run(*, conversation_id: str = "42", status=AgenticRunStatus.RUNNING) -> AgenticRun:
    return AgenticRun.new(
        profile_id="default",
        origin="user",
        channel="voice",
        runtime_id="runtime",
        title="Préparer le résultat",
        conversation_id=conversation_id,
        status=status,
        phase=status.value,
        category=AgenticRequestCategory.AGENTIC_REVERSIBLE,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("utterance", "expected_status", "expected_text"),
    [
        ("Mets-la en pause", AgenticRunStatus.PAUSED, "pause"),
        ("Reprends la tâche", AgenticRunStatus.RUNNING, "reprend"),
        ("Annule la tâche", AgenticRunStatus.CANCELLED, "annulation"),
    ],
)
async def test_voice_controls_target_the_latest_conversation_run(
    monkeypatch: pytest.MonkeyPatch,
    utterance: str,
    expected_status: AgenticRunStatus,
    expected_text: str,
) -> None:
    from api import agentic_processing

    selected = _run(
        status=AgenticRunStatus.PAUSED
        if "Reprends" in utterance
        else AgenticRunStatus.RUNNING
    )
    other = _run(conversation_id="99")
    service = _Service([other, selected])
    monkeypatch.setattr(agentic_processing, "get_agentic_service", lambda: service)

    response = await agentic_processing.maybe_start_agentic_run(
        utterance,
        42,
        channel="voice",
        voice_mode=True,
        persist_assistant=False,
    )

    assert response is not None
    assert response["agentic_run"]["run_id"] == selected.run_id
    assert response["agentic_run"]["status"] == expected_status.value
    assert expected_text in response["text"].lower()
    assert service.runs[other.run_id].status is AgenticRunStatus.RUNNING


@pytest.mark.asyncio
async def test_status_and_result_intents_do_not_create_a_second_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import agentic_processing

    selected = _run()
    service = _Service([selected])
    monkeypatch.setattr(agentic_processing, "get_agentic_service", lambda: service)

    status = await agentic_processing.maybe_start_agentic_run(
        "Où en est la tâche ?",
        42,
        channel="voice",
        voice_mode=True,
        persist_assistant=False,
    )
    result = await agentic_processing.maybe_start_agentic_run(
        "Lis-moi le résultat",
        42,
        channel="voice",
        voice_mode=True,
        persist_assistant=False,
    )

    assert status is not None and status["agentic_run"]["run_id"] == selected.run_id
    assert result is not None and result["agentic_run"]["run_id"] == selected.run_id
    assert "running" in status["text"]
    assert service.created == 0


@pytest.mark.asyncio
async def test_sensitive_voice_approval_requires_exact_id_and_explicit_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import agentic_processing

    selected = _run(status=AgenticRunStatus.AWAITING_APPROVAL)
    approval = ApprovalRequest(
        approval_id=APPROVAL_ID,
        run_id=selected.run_id,
        action="Publier",
        tool="publish",
        summary="Publier le résultat",
    )
    service = _Service([selected], [approval])
    monkeypatch.setattr(agentic_processing, "get_agentic_service", lambda: service)

    ambiguous = await agentic_processing.maybe_start_agentic_run(
        "Oui, vas-y",
        42,
        channel="voice",
        voice_mode=True,
        persist_assistant=False,
    )
    missing_id = await agentic_processing.maybe_start_agentic_run(
        "J'approuve",
        42,
        channel="voice",
        voice_mode=True,
        persist_assistant=False,
    )
    exact = await agentic_processing.maybe_start_agentic_run(
        f"J'approuve l'approbation {APPROVAL_ID}",
        42,
        channel="voice",
        voice_mode=True,
        persist_assistant=False,
        idempotency_key="voice:approval:00000001",
        device="mac-mini",
    )

    assert ambiguous is None
    assert missing_id is not None and "identifiant exact" in missing_id["text"]
    assert exact is not None and "approuvée" in exact["text"]
    assert len(service.decisions) == 1
    _, approval_id, decision, kwargs = service.decisions[0]
    assert approval_id == APPROVAL_ID
    assert decision is ApprovalDecision.APPROVED
    assert kwargs == {
        "decided_by": "voice:mac-mini",
        "decision_id": "voice:approval:00000001",
    }

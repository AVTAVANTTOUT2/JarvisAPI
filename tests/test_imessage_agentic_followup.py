from __future__ import annotations

from types import SimpleNamespace

import pytest

from integrations.imessage import IMessageBridge
from jarvis.agentic.models import (
    AgenticRequestCategory,
    AgenticRun,
    AgenticRunStatus,
    ApprovalDecision,
    ApprovalRequest,
)


@pytest.mark.asyncio
async def test_imessage_relays_agentic_approval_and_terminal_summary(monkeypatch):
    events = [
        SimpleNamespace(
            sequence=1,
            type="agent.approval.requested",
            payload={
                "approval_id": "approval-12345678",
                "spoken_summary": "Envoyer le brouillon",
            },
        ),
        SimpleNamespace(
            sequence=2,
            type="agent.approval.requested",
            payload={
                "approval_id": "approval-12345678",
                "spoken_summary": "Envoyer le brouillon",
            },
        ),
        SimpleNamespace(
            sequence=3,
            type="agent.run.completed",
            payload={"spoken_summary": "Le brouillon a été vérifié."},
        ),
    ]

    class Service:
        def get(self, run_id):
            assert run_id == "run-imessage"
            return SimpleNamespace(
                terminal=True, status=SimpleNamespace(value="completed")
            )

        def events(self, run_id, *, after_sequence=0):
            assert run_id == "run-imessage"
            return [event for event in events if event.sequence > after_sequence]

    monkeypatch.setattr("jarvis.agentic.get_agentic_service", lambda: Service())
    bridge = IMessageBridge("jarvis@example.invalid")
    bridge.running = True
    sent: list[str] = []

    def capture(text: str) -> int:
        sent.append(text)
        return 1

    monkeypatch.setattr(bridge, "_send_message", capture)

    await bridge._follow_agentic_run("run-imessage")

    assert "approval-12345678" in sent[0]
    assert "Envoyer le brouillon" in sent[0]
    assert sent[1] == "Le brouillon a été vérifié."
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_imessage_retries_followup_after_transient_send_failure(monkeypatch):
    events = [
        SimpleNamespace(
            sequence=1,
            type="agent.approval.requested",
            payload={"approval_id": "apr_retry-42", "spoken_summary": "Confirmer"},
        ),
        SimpleNamespace(
            sequence=2,
            type="agent.run.completed",
            payload={"spoken_summary": "Terminé."},
        ),
    ]

    class Service:
        def get(self, _run_id):
            return SimpleNamespace(
                terminal=True, status=SimpleNamespace(value="completed")
            )

        def events(self, _run_id, *, after_sequence=0):
            return [event for event in events if event.sequence > after_sequence]

    async def no_wait(_seconds):
        return None

    attempts: list[str] = []

    def flaky_send(text: str) -> int:
        attempts.append(text)
        return 0 if len(attempts) == 1 else 1

    monkeypatch.setattr("jarvis.agentic.get_agentic_service", lambda: Service())
    monkeypatch.setattr("integrations.imessage.asyncio.sleep", no_wait)
    bridge = IMessageBridge("jarvis@example.invalid")
    bridge.running = True
    monkeypatch.setattr(bridge, "_send_message", flaky_send)

    await bridge._follow_agentic_run("run-retry")

    assert attempts[0] == attempts[1]
    assert "apr_retry-42" in attempts[1]
    assert attempts[2] == "Terminé."


@pytest.mark.asyncio
async def test_imessage_reuses_conversation_checkpoint_and_idempotency_after_restart(
    monkeypatch,
):
    resolved: dict[str, int] = {}
    processing_calls: list[tuple[str, int, dict]] = []
    saved: list[tuple[int, str, str]] = []

    def resolve(checkpoint_id, *, agent, create):
        assert agent == "orchestrator"
        assert create is True
        resumed = checkpoint_id in resolved
        return resolved.setdefault(checkpoint_id, 73), resumed

    async def process(text, conversation_id, **kwargs):
        processing_calls.append((text, conversation_id, kwargs))
        return {"text": "Réponse stable"}

    monkeypatch.setattr("config.IMESSAGE_PREFIX", "")
    monkeypatch.setattr("database.resolve_conversation_checkpoint", resolve)
    monkeypatch.setattr(
        "database.save_message",
        lambda conversation_id, role, text: saved.append((conversation_id, role, text)),
    )
    monkeypatch.setattr("api.chat_processing._process_message_internal", process)

    first_bridge = IMessageBridge("jarvis@example.invalid")
    restarted_bridge = IMessageBridge("jarvis@example.invalid")
    first = await first_bridge._process_message(
        "Prépare le rapport", idempotency_key="imessage:91"
    )
    replay = await restarted_bridge._process_message(
        "Prépare le rapport",
        idempotency_key="imessage:91",
    )

    assert first == replay == "Réponse stable"
    assert (
        first_bridge.conversation_checkpoint_id
        == restarted_bridge.conversation_checkpoint_id
    )
    assert first_bridge.cursor_name == restarted_bridge.cursor_name
    assert {conversation_id for _, conversation_id, _ in processing_calls} == {73}
    assert {call[2]["confirmation_session_id"] for call in processing_calls} == {
        f"imessage:{first_bridge.conversation_checkpoint_id}"
    }
    assert {call[2]["agentic_idempotency_key"] for call in processing_calls} == {
        "imessage:91"
    }
    assert saved == [
        (73, "user", "Prépare le rapport"),
        (73, "user", "Prépare le rapport"),
    ]


@pytest.mark.asyncio
async def test_imessage_accepts_bounded_opaque_approval_ids(monkeypatch):
    from api import agentic_processing

    opaque_id = "apr_Q7Z-opaque.v2:42"
    run = AgenticRun.new(
        profile_id="default",
        origin="user",
        channel="imessage",
        runtime_id="runtime",
        title="Préparer le rapport",
        conversation_id="73",
        status=AgenticRunStatus.AWAITING_APPROVAL,
        phase="awaiting_approval",
        category=AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT,
    )
    approval = ApprovalRequest(
        approval_id=opaque_id,
        run_id=run.run_id,
        action="Envoyer",
        tool="send",
        summary="Envoyer le rapport",
    )

    class Service:
        def __init__(self):
            self.decisions = []

        def list(self, **_kwargs):
            return [run]

        def get(self, run_id):
            return run if run_id == run.run_id else None

        def approvals(self, run_id):
            return [approval] if run_id == run.run_id else []

        async def decide_approval(self, run_id, approval_id, decision, **kwargs):
            self.decisions.append((run_id, approval_id, decision, kwargs))
            return approval

    service = Service()
    monkeypatch.setattr(agentic_processing, "get_agentic_service", lambda: service)

    accepted = await agentic_processing.maybe_start_agentic_run(
        f"J’approuve l’approbation {opaque_id}",
        73,
        channel="imessage",
        device="bridge",
        voice_mode=False,
        idempotency_key="imessage:92",
        persist_assistant=False,
    )
    oversized = await agentic_processing.maybe_start_agentic_run(
        f"J’approuve l’approbation {'a' * 129}",
        73,
        channel="imessage",
        device="bridge",
        voice_mode=False,
        idempotency_key="imessage:93",
        persist_assistant=False,
    )

    assert accepted is not None and "approuvée" in accepted["text"]
    assert oversized is not None and "identifiant exact" in oversized["text"]
    assert service.decisions == [
        (
            run.run_id,
            opaque_id,
            ApprovalDecision.APPROVED,
            {"decided_by": "imessage:bridge", "decision_id": "imessage:92"},
        )
    ]

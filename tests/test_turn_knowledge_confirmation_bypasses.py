"""Non-régression des snapshots knowledge aux frontières de confirmation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

import api.chat_processing as chat_processing
import api.misc_integrations as misc_integrations
import api.router_mobile_chat as mobile_chat
import api.ws_action_messages as ws_actions
from api.chat_confirmation import resolve_pending_confirmation


@dataclass
class _Snapshot:
    context: dict[str, Any] = field(
        default_factory=lambda: {
            "retrieval_context": '<UNTRUSTED_DATA source="KNOWLEDGE_RETRIEVAL">x</UNTRUSTED_DATA>',
            "__retrieval_done": True,
        }
    )
    knowledge: dict[str, Any] = field(
        default_factory=lambda: {
            "snapshot_id": "turn_test",
            "status": "ok",
            "references": [{"uid": "email:test"}],
        }
    )

    def to_context(self) -> dict[str, Any]:
        return self.context

    def public_payload(self) -> dict[str, Any]:
        return self.knowledge


class _Socket:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_json(self, message: dict[str, Any]) -> None:
        self.messages.append(message)


@pytest.mark.asyncio
async def test_internal_confirmation_prepares_once_and_reuses_followup_context() -> (
    None
):
    action = {"type": "mail_read", "query": "Grégoire"}
    snapshot = _Snapshot()
    prepare = AsyncMock(return_value=snapshot)
    execute = AsyncMock(return_value={"ok": True, "message": "mail trouvé"})
    followup = AsyncMock(
        return_value={"response": "Grégoire a confirmé.", "emotion": "neutral"}
    )

    result = await resolve_pending_confirmation(
        "oui",
        7,
        confirmation_session_id="session:7",
        voice_mode=False,
        persist_assistant=False,
        trace=None,
        execute_action_fn=execute,
        orchestrator_handle_fn=followup,
        save_message_fn=Mock(),
        update_conversation_activity_fn=Mock(),
        mark_voice_trace_fn=Mock(),
        actions_with_followup={"mail_read"},
        peek_pending_proposal_fn=Mock(return_value={"proposal_id": "p"}),
        pop_pending_action_fn=Mock(return_value=action),
        imperative_confirmation_fn=Mock(return_value=True),
        unmatched_confirmation_reply_fn=Mock(),
        format_action_result_for_followup_fn=Mock(return_value="résultat filtré"),
        finalize_assistant_display_text_fn=lambda value: value,
        prepare_turn_fn=prepare,
    )

    prepare.assert_awaited_once()
    assert prepare.await_args.kwargs["interaction_mode"] == "chat"
    execute.assert_awaited_once_with(action)
    assert followup.await_args.kwargs["context"] is snapshot.context
    assert result is not None
    assert result["knowledge"] == snapshot.knowledge


@pytest.mark.asyncio
async def test_chat_processing_injects_its_prepare_turn_dependency(monkeypatch) -> None:
    resolver = AsyncMock(return_value={"text": "confirmé", "knowledge": {}})
    monkeypatch.setattr(chat_processing, "resolve_pending_confirmation", resolver)
    monkeypatch.setattr(
        chat_processing, "maybe_start_agentic_run", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        chat_processing,
        "maybe_handle_legacy_agentic_chat",
        AsyncMock(return_value=None),
    )

    result = await chat_processing._process_message_internal(
        "oui",
        8,
        persist_assistant=False,
    )

    assert result["text"] == "confirmé"
    assert resolver.await_args.kwargs["prepare_turn_fn"] is chat_processing.prepare_turn


@pytest.mark.asyncio
async def test_ws_confirmation_reuses_one_snapshot_for_followup(monkeypatch) -> None:
    action = {"type": "mail_read", "query": "Grégoire"}
    snapshot = _Snapshot()
    prepare = AsyncMock(return_value=snapshot)
    execute = AsyncMock(return_value={"ok": True, "message": "mail trouvé"})
    followup = AsyncMock(return_value={"response": "Résumé du mail"})
    monkeypatch.setattr(
        ws_actions, "consume_pending_proposal", Mock(return_value=action)
    )
    monkeypatch.setattr(ws_actions, "prepare_turn", prepare)
    monkeypatch.setattr(ws_actions, "execute_action", execute)
    monkeypatch.setattr(ws_actions.orchestrator, "handle", followup)
    monkeypatch.setattr(ws_actions, "save_message", Mock())
    monkeypatch.setattr(ws_actions, "_schedule_llm_log", Mock())
    socket = _Socket()

    handled = await ws_actions.handle_ws_action_decision(
        socket,
        {"type": "action_confirm", "proposal_id": "A" * 43},
        conversation_id=9,
        confirmation_session_id="session:9",
    )

    assert handled is True
    prepare.assert_awaited_once()
    assert prepare.await_args.kwargs["interaction_mode"] == "stream"
    execute.assert_awaited_once_with(action)
    assert followup.await_args.kwargs["context"] is snapshot.context
    action_message = next(
        item for item in socket.messages if item.get("type") == "action_result"
    )
    followup_message = next(
        item for item in socket.messages if item.get("type") == "response_followup"
    )
    assert action_message["knowledge"] == snapshot.knowledge
    assert followup_message["knowledge"] == snapshot.knowledge


@pytest.mark.asyncio
async def test_mobile_confirmation_returns_the_prepared_knowledge(
    monkeypatch,
) -> None:
    action = {"type": "calendar_create", "summary": "Déjeuner"}
    snapshot = _Snapshot()
    prepare = AsyncMock(return_value=snapshot)
    execute = AsyncMock(return_value={"ok": True, "message": "créé"})
    monkeypatch.setattr(
        mobile_chat, "get_conversation_detail", Mock(return_value={"id": 10})
    )
    monkeypatch.setattr(
        mobile_chat, "consume_pending_proposal", Mock(return_value=action)
    )
    monkeypatch.setattr(mobile_chat, "prepare_turn", prepare)
    monkeypatch.setattr(mobile_chat, "execute_action", execute)
    monkeypatch.setattr(mobile_chat, "save_message", Mock())
    body = mobile_chat.MobileChatConfirmationRequest(
        conversation_id=10,
        proposal_id="B" * 43,
        confirmed=True,
    )

    result = await mobile_chat.api_mobile_chat_confirm(
        body,
        {"device_id": "device-10"},
    )

    prepare.assert_awaited_once()
    assert prepare.await_args.kwargs["interaction_mode"] == "chat"
    execute.assert_awaited_once_with(action)
    assert result["knowledge"] == snapshot.knowledge


@pytest.mark.asyncio
async def test_mission_prompt_prepares_once_and_transports_knowledge(
    monkeypatch,
) -> None:
    snapshot = _Snapshot()
    prepare = AsyncMock(return_value=snapshot)
    handle = AsyncMock(return_value={"response": "Mission comprise"})
    monkeypatch.setattr(misc_integrations, "prepare_turn", prepare)
    monkeypatch.setattr(misc_integrations.orchestrator, "handle", handle)

    result = await misc_integrations.mission_prompt(
        {"message": "Résume le mail de Grégoire", "conversation_id": "11"}
    )

    prepare.assert_awaited_once_with(
        "Résume le mail de Grégoire",
        11,
        interaction_mode="chat",
    )
    assert handle.await_args.kwargs["context"] is snapshot.context
    assert result["knowledge"] == snapshot.knowledge

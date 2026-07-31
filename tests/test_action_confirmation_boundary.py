"""Régressions sécurité de la frontière proposition → confirmation → action."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, patch

import pytest

from api.action_confirmations import (
    ProposalError,
    cancel_pending_proposal,
    consume_pending_proposal,
    consume_text_confirmation,
    reset_pending_proposals_for_tests,
    store_pending_proposal,
)
from api.chat_actions import _extract_action_from_text, _format_action_result_for_followup
from api.ws_action_messages import handle_ws_action_decision


@pytest.fixture(autouse=True)
def isolated_proposals():
    reset_pending_proposals_for_tests()
    yield
    reset_pending_proposals_for_tests()


class _Socket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.messages.append(message)


@pytest.mark.asyncio
async def test_action_confirm_rejects_arbitrary_client_payload() -> None:
    socket = _Socket()
    execute = AsyncMock(return_value={"ok": True})
    with patch("api.ws_action_messages.execute_action", execute), patch(
        "api.ws_action_messages._schedule_llm_log"
    ):
        handled = await handle_ws_action_decision(
            socket,
            {
                "type": "action_confirm",
                "proposal_id": "A" * 43,
                "action": {"type": "calendar_create", "summary": "attaque"},
            },
            conversation_id=7,
            confirmation_session_id="session:victim",
        )
    assert handled is True
    assert socket.messages == [{"type": "error", "message": "proposal_id invalide"}]
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_action_confirmation_is_one_shot_and_uses_server_action() -> None:
    public = store_pending_proposal(
        {"type": "task", "title": "Action serveur"},
        conversation_id=8,
        session_id="session:one",
    )
    socket = _Socket()
    execute = AsyncMock(return_value={"ok": True, "message": "créée"})
    with patch("api.ws_action_messages.execute_action", execute), patch(
        "api.ws_action_messages._schedule_llm_log"
    ):
        first = await handle_ws_action_decision(
            socket,
            {"type": "action_confirm", "proposal_id": public["proposal_id"]},
            conversation_id=8,
            confirmation_session_id="session:one",
        )
        replay = await handle_ws_action_decision(
            socket,
            {"type": "action_confirm", "proposal_id": public["proposal_id"]},
            conversation_id=8,
            confirmation_session_id="session:one",
        )
    assert first is replay is True
    execute.assert_awaited_once_with({
        "type": "task",
        "title": "Action serveur",
        "confirmed": True,
    })
    assert socket.messages[-1]["type"] == "error"
    assert "déjà utilisée" in socket.messages[-1]["message"]


def test_proposal_is_bound_to_session_and_conversation() -> None:
    public = store_pending_proposal(
        {"type": "open_app", "name": "Safari"},
        conversation_id=9,
        session_id="session:owner",
    )
    with pytest.raises(ProposalError, match="session ou conversation"):
        consume_pending_proposal(
            public["proposal_id"], conversation_id=9, session_id="session:attacker",
        )
    with pytest.raises(ProposalError, match="session ou conversation"):
        consume_pending_proposal(
            public["proposal_id"], conversation_id=10, session_id="session:owner",
        )
    assert consume_pending_proposal(
        public["proposal_id"], conversation_id=9, session_id="session:owner",
    )["name"] == "Safari"


def test_atomic_consumption_has_exactly_one_winner() -> None:
    public = store_pending_proposal(
        {"type": "task", "title": "une fois"},
        conversation_id=11,
        session_id="session:race",
    )

    def consume() -> bool:
        try:
            consume_pending_proposal(
                public["proposal_id"], conversation_id=11, session_id="session:race",
            )
            return True
        except ProposalError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: consume(), range(2)))
    assert sorted(outcomes) == [False, True]


@pytest.mark.parametrize("refusal", ["lance pas", "exécute pas", "execute pas", "non"])
def test_textual_negation_revokes_then_yes_cannot_execute(refusal: str) -> None:
    public = store_pending_proposal(
        {"type": "task", "title": "ne pas créer"},
        conversation_id=12,
        session_id="session:text",
    )
    assert consume_text_confirmation(
        refusal, conversation_id=12, session_id="session:text",
    ) is None
    assert consume_text_confirmation(
        "oui", conversation_id=12, session_id="session:text",
    ) is None
    assert cancel_pending_proposal(
        public["proposal_id"], conversation_id=12, session_id="session:text",
    ) is False


def test_textual_confirmation_has_no_prefix_matching() -> None:
    store_pending_proposal(
        {"type": "task", "title": "ne pas créer"},
        conversation_id=13,
        session_id="session:text",
    )
    assert consume_text_confirmation(
        "oui mais attends", conversation_id=13, session_id="session:text",
    ) is None


def test_inline_json_example_is_not_an_action() -> None:
    response = (
        "Voici un exemple de réponse LLM : "
        '{"type":"calendar_create","summary":"Ne pas exécuter"}.'
    )
    action, clean = _extract_action_from_text(response)
    assert action is None
    assert clean == response


def test_terminal_and_clipboard_secrets_never_enter_followup_prompt() -> None:
    secret = "LOCAL_SENSITIVE_VALUE_42"
    terminal = _format_action_result_for_followup(
        {"type": "terminal", "command": "cat secret.txt"},
        {
            "ok": True,
            "output": secret,
            "stdout": secret,
            "stderr": secret,
            "code": [{"language": "shell", "code": "cat secret.txt"}],
            "impact_analysis": {"max_risk": "low"},
        },
    )
    clipboard = _format_action_result_for_followup(
        {"type": "clipboard", "action": "get"},
        {"ok": True, "content": secret},
    )
    assert secret not in terminal
    assert "cat secret.txt" not in terminal
    assert secret not in clipboard

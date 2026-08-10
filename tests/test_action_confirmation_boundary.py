"""Régressions sécurité de la frontière proposition → confirmation → action."""

from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, patch

import pytest

from api.action_confirmations import (
    ProposalError,
    cancel_pending_proposal,
    consume_pending_proposal,
    consume_text_confirmation,
    is_exact_confirmation,
    is_imperative_confirmation,
    is_valid_proposal_id,
    peek_pending_proposal,
    reset_pending_proposals_for_tests,
    store_pending_proposal,
    unmatched_confirmation_reply,
)
from api.chat_actions import _extract_action_from_text, _format_action_result_for_followup
from api.ws_action_messages import handle_ws_action_decision
from integrations.shell_safety import (
    ShellPlanError,
    get_shell_plan,
    prepare_shell_plan,
    reset_shell_plans_for_tests,
)


@pytest.fixture(autouse=True)
def isolated_proposals(tmp_path, monkeypatch):
    monkeypatch.setattr("config.LLM_SHELL_WORKSPACE", str(tmp_path / "shell"))
    monkeypatch.setattr("config.LLM_SHELL_PLAN_TTL_SECONDS", 600)
    reset_pending_proposals_for_tests()
    reset_shell_plans_for_tests()
    yield
    reset_pending_proposals_for_tests()
    reset_shell_plans_for_tests()


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
    secret = "sk-actionConfirmationBoundary123456789"
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
    assert "UNTRUSTED_DATA:ACTION_RESULT_TERMINAL" in terminal
    assert secret not in clipboard


@pytest.mark.parametrize(
    "phrase",
    ["oui", "Oui!", "OK", "d'accord", "Oui, vas-y.", "  go  ", "pourquoi pas"],
)
def test_exact_confirmation_tolerates_stt_punctuation(phrase: str) -> None:
    """Les moteurs STT ponctuent (« Oui, vas-y. ») ; ce n'est pas une nouvelle intention.

    « pourquoi pas » est dans l'allowlist et ne doit pas être tué par un
    faux positif sur le jeton « pas ».
    """
    assert is_exact_confirmation(phrase) is True


@pytest.mark.parametrize(
    "phrase",
    ["", "   ", "oui mais attends", "lance pas", "non", "vas-y demain", "oui\noui"],
)
def test_exact_confirmation_rejects_partial_or_negated_phrases(phrase: str) -> None:
    """Les refus et les phrases partielles ne confirment jamais — exactitude stricte."""
    assert is_exact_confirmation(phrase) is False


def test_soft_yes_is_exact_but_not_imperative() -> None:
    """« oui » confirme un plan en attente ; sans plan, ce n'est pas une injonction."""
    assert is_exact_confirmation("oui") is True
    assert is_imperative_confirmation("oui") is False
    assert is_imperative_confirmation("vas-y") is True
    assert is_imperative_confirmation("lance pas") is False


def test_proposal_id_must_match_token_urlsafe_32() -> None:
    valid = secrets.token_urlsafe(32)
    assert len(valid) == 43
    assert is_valid_proposal_id(valid) is True
    assert is_valid_proposal_id("A" * 42) is False
    assert is_valid_proposal_id("A" * 44) is False
    assert is_valid_proposal_id("A" * 42 + "!") is False
    assert is_valid_proposal_id(None) is False
    assert is_valid_proposal_id(123) is False


def test_unmatched_confirmation_reply_never_claims_success() -> None:
    reply = unmatched_confirmation_reply()
    assert reply["action"] is None
    assert reply["action_result"]["ok"] is False
    assert reply["action_result"]["error"] == "no_pending_action"
    assert reply["cost"] == 0.0
    assert "aucune action en attente" in reply["text"].lower()


def test_replacing_a_proposal_revokes_the_previous_shell_plan() -> None:
    """Une proposition abandonnée ne doit laisser aucun plan shell consommable."""
    plan = prepare_shell_plan(["pwd"])
    first = store_pending_proposal(
        {"type": "terminal", "shell_plan_id": plan["plan_id"], "command": "pwd"},
        conversation_id=21,
        session_id="session:replace",
    )
    assert get_shell_plan(plan["plan_id"])["plan_id"] == plan["plan_id"]

    second = store_pending_proposal(
        {"type": "task", "title": "autre"},
        conversation_id=21,
        session_id="session:replace",
    )
    assert second["proposal_id"] != first["proposal_id"]
    with pytest.raises(ShellPlanError, match="inconnu|expiré|utilisé"):
        get_shell_plan(plan["plan_id"])
    assert peek_pending_proposal(
        conversation_id=21, session_id="session:replace",
    )["type"] == "task"


def _freeze_proposal_clock(
    monkeypatch: pytest.MonkeyPatch, *, ttl_seconds: int = 30,
) -> dict[str, float]:
    import api.action_confirmations as action_confirmations

    clock = {"now": 1_000.0}
    monkeypatch.setattr(
        action_confirmations.time, "monotonic", lambda: clock["now"],
    )
    monkeypatch.setattr(
        action_confirmations.config, "LLM_SHELL_PLAN_TTL_SECONDS", ttl_seconds,
    )
    return clock


def test_peek_expired_proposal_revokes_shell_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _freeze_proposal_clock(monkeypatch)
    plan = prepare_shell_plan(["pwd"])
    store_pending_proposal(
        {"type": "terminal", "shell_plan_id": plan["plan_id"], "command": "pwd"},
        conversation_id=22,
        session_id="session:peek-expiry",
    )
    assert peek_pending_proposal(
        conversation_id=22, session_id="session:peek-expiry",
    ) is not None

    clock["now"] = 1_040.0
    assert peek_pending_proposal(
        conversation_id=22, session_id="session:peek-expiry",
    ) is None
    with pytest.raises(ShellPlanError, match="inconnu|expiré|utilisé"):
        get_shell_plan(plan["plan_id"])


def test_text_confirmation_on_expired_proposal_revokes_shell_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _freeze_proposal_clock(monkeypatch)
    plan = prepare_shell_plan(["pwd"])
    store_pending_proposal(
        {"type": "terminal", "shell_plan_id": plan["plan_id"], "command": "pwd"},
        conversation_id=23,
        session_id="session:text-expiry",
    )

    clock["now"] = 1_040.0
    assert consume_text_confirmation(
        "oui", conversation_id=23, session_id="session:text-expiry",
    ) is None
    with pytest.raises(ShellPlanError, match="inconnu|expiré|utilisé"):
        get_shell_plan(plan["plan_id"])


def test_consume_expired_proposal_revokes_shell_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _freeze_proposal_clock(monkeypatch)
    plan = prepare_shell_plan(["pwd"])
    public = store_pending_proposal(
        {"type": "terminal", "shell_plan_id": plan["plan_id"], "command": "pwd"},
        conversation_id=24,
        session_id="session:consume-expiry",
    )

    clock["now"] = 1_040.0
    with pytest.raises(ProposalError, match="expir"):
        consume_pending_proposal(
            public["proposal_id"],
            conversation_id=24,
            session_id="session:consume-expiry",
        )
    with pytest.raises(ShellPlanError, match="inconnu|expiré|utilisé"):
        get_shell_plan(plan["plan_id"])

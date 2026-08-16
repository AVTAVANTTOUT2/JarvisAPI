"""Non-régressions du retrieval partagé entre chat, voix et runtime agentique."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from jarvis.security.llm_data_boundary import wrap_untrusted_data


async def _no_live_refresh(_request):
    return {}


@pytest.mark.asyncio
async def test_chat_context_runs_one_bounded_retrieval_with_recent_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import chat_context

    current = "Retrouve le mail de Grégoire"
    monkeypatch.setattr(
        chat_context,
        "get_conversation_history",
        lambda _conversation_id, *, limit: [
            {"role": "user", "content": "Il s'est passé quoi hier ?"},
            {"role": "assistant", "content": "Je vérifie."},
            {"role": "user", "content": "Résume mes mails"},
            {"role": "assistant", "content": "Je regarde."},
            {"role": "user", "content": current},
        ],
    )
    result = SimpleNamespace(
        status="complete",
        hits=[
            {
                "uid": "email:42",
                "source_type": "email",
                "source_id": "42",
                "content": "contenu interne à ne pas recopier dans les références",
            }
        ],
        verified_sources=("email", "message"),
        unavailable_sources=(),
        latency_ms=17,
    )
    requests = []
    live_requests = []

    async def _refresh(request):
        live_requests.append(request)
        return {"email": "ok"}

    def _search(request):
        requests.append(request)
        return result

    monkeypatch.setattr(chat_context, "refresh_live_sources", _refresh)
    monkeypatch.setattr(chat_context, "search_knowledge", _search)
    monkeypatch.setattr(
        chat_context,
        "format_retrieval_context",
        lambda value, *, max_chars: wrap_untrusted_data(
            "KNOWLEDGE_RETRIEVAL",
            "status: complete\nsource: email\ndate: 2026-08-15\nexpéditeur: Grégoire",
            max_chars=max_chars,
        ),
    )

    context: dict = {}
    await chat_context._attach_retrieval_context(
        context,
        text=current,
        conversation_id=9,
        interaction_mode="voice",
    )

    assert len(requests) == 1
    assert live_requests == requests
    request = requests[0]
    assert request.query == current
    assert request.conversation_id == 9
    assert request.interaction_mode == "voice"
    assert request.max_candidates == 20
    assert request.max_hits == 8
    assert request.char_budget == 8_000
    assert list(request.recent_user_turns) == [
        "Il s'est passé quoi hier ?",
        "Résume mes mails",
    ]
    assert context["history"][-1]["content"] == "Je regarde."
    assert context["retrieval_context"].startswith(
        "[UNTRUSTED_DATA:KNOWLEDGE_RETRIEVAL]"
    )
    assert context["__retrieval_references"] == [
        {"uid": "email:42", "source_type": "email", "source_id": "42"}
    ]
    assert context["__retrieval_live"] == {"email": "ok"}
    assert context["__retrieval_done"] is True


@pytest.mark.asyncio
async def test_chat_context_keeps_degraded_status_and_source_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import chat_context
    from jarvis.retrieval import RetrievalResult

    monkeypatch.setattr(
        chat_context,
        "get_conversation_history",
        lambda _conversation_id, *, limit: [],
    )

    async def _mail_unavailable(_request):
        return {"email": "unavailable"}

    monkeypatch.setattr(chat_context, "refresh_live_sources", _mail_unavailable)
    monkeypatch.setattr(
        chat_context,
        "search_knowledge",
        lambda _request: RetrievalResult(
            status="ok",
            query="Grégoire m'a écrit ?",
            hits=(),
            candidate_count=0,
            verified_sources=("conversation", "email"),
            unavailable_sources=(),
        ),
    )

    context: dict = {}
    await chat_context._attach_retrieval_context(
        context,
        text="Grégoire m'a écrit ?",
        conversation_id=9,
        interaction_mode="chat",
    )

    block = context["retrieval_context"]
    assert block.startswith("[UNTRUSTED_DATA:KNOWLEDGE_RETRIEVAL]")
    envelope = json.loads(block.splitlines()[1])
    payload = json.loads(envelope["content"])
    assert payload["status"] == "degraded"
    assert payload["verified_sources"] == ["conversation", "email"]
    assert payload["unavailable_sources"] == ["email"]
    assert context["__retrieval"]["diagnostics"] == ["live:email:unavailable"]


@pytest.mark.asyncio
async def test_chat_context_exposes_retrieval_failure_without_claiming_no_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import chat_context

    monkeypatch.setattr(
        chat_context,
        "get_conversation_history",
        lambda _conversation_id, *, limit: [],
    )
    monkeypatch.setattr(chat_context, "refresh_live_sources", _no_live_refresh)

    def _offline(_request):
        raise RuntimeError("offline")

    monkeypatch.setattr(chat_context, "search_knowledge", _offline)

    context: dict = {}
    await chat_context._attach_retrieval_context(
        context,
        text="Ai-je reçu un message ?",
        conversation_id=9,
        interaction_mode="chat",
    )

    assert context["__retrieval"]["status"] == "unavailable"
    assert context["__retrieval"]["unavailable_sources"] == ["retrieval"]
    assert context["retrieval_context"].startswith(
        "[UNTRUSTED_DATA:KNOWLEDGE_RETRIEVAL]"
    )
    assert "indisponible" in context["retrieval_context"]


def test_every_base_agent_receives_the_same_untrusted_retrieval_block() -> None:
    from agents import BaseAgent

    class _AgentWithoutPlaceholder(BaseAgent):
        name = "test-no-retrieval-placeholder"
        inject_persona = False

        def load_prompt(self) -> str:
            return "Prompt agent sans placeholder spécialisé."

        async def handle(self, user_message, conversation_id=None, context=None):
            return {"response": user_message}

    retrieval_context = wrap_untrusted_data(
        "RETRIEVAL_CONTEXT",
        "status: complete\nsource: email\nsubject: Test JARVIS",
        max_chars=8_000,
    )
    prompt = _AgentWithoutPlaceholder().build_system_prompt(
        {
            "retrieval_context": retrieval_context,
            "__retrieval": {"status": "complete"},
            "history": [{"role": "user", "content": "secret historique"}],
        }
    )

    assert prompt.count("[UNTRUSTED_DATA:RETRIEVAL_CONTEXT]") == 1
    assert "subject: Test JARVIS" in prompt
    assert "secret historique" not in prompt
    assert "__retrieval" not in prompt


def test_agentic_context_is_bounded_to_references_and_recent_history() -> None:
    from api.agentic_processing import _agentic_memory_context

    selected = _agentic_memory_context(
        {
            "retrieval_context": "[UNTRUSTED_DATA:RETRIEVAL_CONTEXT]...",
            "__retrieval_references": [
                {"uid": f"message:{index}", "source_type": "message"}
                for index in range(12)
            ],
            "__retrieval": {"status": "complete"},
            "history": [
                {
                    "role": "user" if index % 2 == 0 else "assistant",
                    "content": str(index),
                }
                for index in range(10)
            ],
        }
    )

    assert len(selected["retrieval_references"]) == 8
    assert selected["retrieval_status"] == {"status": "complete"}
    assert len(selected["conversation_history"]) == 6
    assert selected["conversation_history"][-1]["role"] == "assistant"


def test_loop_runtime_receives_references_status_and_recent_history() -> None:
    from agents.autonomous_loop import _loop_selected_context

    selected = _loop_selected_context(
        {
            "retrieval_context": "[UNTRUSTED_DATA:RETRIEVAL_CONTEXT]...",
            "__retrieval_references": [
                {"uid": f"recording:{index}", "source_type": "recording"}
                for index in range(12)
            ],
            "__retrieval": {"status": "degraded", "unavailable_sources": ["email"]},
            "history": [
                {
                    "role": "user" if index % 2 == 0 else "assistant",
                    "content": str(index),
                }
                for index in range(10)
            ],
        },
        {"domain": "general"},
    )

    assert len(selected["retrieval_references"]) == 8
    assert selected["retrieval_status"]["status"] == "degraded"
    assert len(selected["conversation_history"]) == 6


@pytest.mark.asyncio
async def test_rest_loop_reuses_the_single_enriched_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import chat_processing

    shared_context = {
        "retrieval_context": "[UNTRUSTED_DATA:RETRIEVAL_CONTEXT]...",
        "__retrieval_references": [{"uid": "email:42"}],
        "history": [{"role": "user", "content": "ancien tour"}],
    }
    build_calls = []
    received = []

    async def _build(text, conversation_id, *, interaction_mode):
        build_calls.append((text, conversation_id, interaction_mode))
        return shared_context

    async def _agentic(_text, _conversation_id, **kwargs):
        assert kwargs["enriched_context"] is shared_context
        return None

    async def _legacy_loop(_task, _conversation_id, **kwargs):
        received.append(kwargs["context"])
        return {"text": "terminé", "emotion": "neutral", "agent": "loop", "cost": 0.0}

    monkeypatch.setattr(chat_processing, "_build_enriched_context", _build)
    monkeypatch.setattr(chat_processing, "maybe_start_agentic_run", _agentic)
    monkeypatch.setattr(chat_processing, "_run_loop_mode_internal", _legacy_loop)
    monkeypatch.setattr(chat_processing, "save_message", lambda *args, **kwargs: None)

    response = await chat_processing._process_message_internal(
        "/loop retrouve le mail de Grégoire",
        9,
    )

    assert response["text"] == "terminé"
    assert build_calls == [("retrouve le mail de Grégoire", 9, "chat")]
    assert received == [shared_context]


@pytest.mark.parametrize(
    ("voice_mode", "stream", "expected_mode"),
    [(False, True, "stream"), (True, True, "voice")],
)
@pytest.mark.asyncio
async def test_websocket_loop_builds_once_before_agentic_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    voice_mode: bool,
    stream: bool,
    expected_mode: str,
) -> None:
    from api import ws_messages

    class _WebSocket:
        async def send_json(self, _payload):
            return None

    shared_context = {"retrieval_context": "[UNTRUSTED_DATA:RETRIEVAL_CONTEXT]..."}
    build_calls = []

    async def _build(text, conversation_id, *, interaction_mode):
        build_calls.append((text, conversation_id, interaction_mode))
        return shared_context

    async def _agentic(_ws, _text, _conversation_id, **kwargs):
        assert kwargs["enriched_context"] is shared_context
        return {"emotion": "neutral", "response": "lancée"}

    monkeypatch.setattr(ws_messages, "_build_enriched_context", _build)
    monkeypatch.setattr(ws_messages, "maybe_send_agentic_run", _agentic)
    monkeypatch.setattr(ws_messages, "save_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        ws_messages,
        "update_conversation_activity",
        lambda *args, **kwargs: None,
    )

    response = await ws_messages._process_message(
        _WebSocket(),
        "/loop retrouve le dernier mail",
        9,
        voice_mode=voice_mode,
        stream=stream,
        confirmation_session_id="test",
    )

    assert response["response"] == "lancée"
    assert build_calls == [("/loop retrouve le dernier mail", 9, expected_mode)]


@pytest.mark.asyncio
async def test_productivity_agent_does_not_collect_mail_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents import productivity

    class _Mail:
        calls = 0

        @staticmethod
        def is_available() -> bool:
            return True

        @classmethod
        async def get_unread(cls, _limit):
            cls.calls += 1
            return []

    class _Unavailable:
        @staticmethod
        def is_available() -> bool:
            return False

    monkeypatch.setattr(productivity, "mail_client", _Mail)
    monkeypatch.setattr(productivity, "calendar_client", _Unavailable)
    monkeypatch.setattr(productivity, "weather", _Unavailable)
    monkeypatch.setattr(productivity, "get_tasks", lambda: [])
    monkeypatch.setattr(productivity, "get_unread_notifications", lambda *, limit: [])

    collected = await productivity.ProductivityAgent()._collect_pro_context(
        skip_mail=True
    )

    assert _Mail.calls == 0
    assert collected["emails_context"] == ""


def test_orchestrator_fixed_context_is_only_the_stable_profile() -> None:
    from agents.orchestrator import OrchestratorAgent

    context = OrchestratorAgent().build_context()

    assert "[PROFIL_STABLE]" in context["memory_context"]
    assert "[RECENT_EPISODES]" not in context["memory_context"]
    assert "[USER_FACTS]" not in context["memory_context"]
    assert len(context["memory_context"]) < 1_000


@pytest.mark.asyncio
async def test_direct_orchestrator_dispatch_retrieves_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agents.orchestrator as orchestrator_module
    import jarvis.retrieval as retrieval_module
    from jarvis.retrieval import RetrievalResult
    from jarvis.retrieval import live_sources

    calls = 0

    def _search(request):
        nonlocal calls
        calls += 1
        return RetrievalResult(
            status="ok",
            query=request.query,
            verified_sources=("email",),
        )

    async def _refresh(_request):
        return {}

    monkeypatch.setattr(retrieval_module, "search_knowledge", _search)
    monkeypatch.setattr(live_sources, "refresh_live_sources", _refresh)
    monkeypatch.setattr(orchestrator_module, "get_agent", lambda _name: None)

    orchestrator = orchestrator_module.OrchestratorAgent()
    context, _agent = await orchestrator._prepare_dispatch_context(
        "retrouve le mail de Grégoire",
        None,
        "INFO",
        voice_mode=False,
    )
    assert calls == 1
    assert context["__retrieval_done"] is True
    assert "KNOWLEDGE_RETRIEVAL" in context["retrieval_context"]

    await orchestrator._prepare_dispatch_context(
        "retrouve le mail de Grégoire",
        None,
        "INFO",
        voice_mode=False,
        base_context=context,
    )
    assert calls == 1

"""Comptabilité des flux LLM : usage fournisseur ou estimation explicite."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeClient:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.payload: dict | None = None

    def stream(self, method: str, url: str, *, json: dict, headers: dict):
        assert method == "POST"
        assert url.endswith("/v1/chat/completions")
        assert headers["Authorization"].startswith("Bearer ")
        self.payload = json
        return _FakeStreamResponse(self._lines)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}"


@pytest.mark.asyncio
async def test_chat_stream_reports_provider_usage(monkeypatch) -> None:
    import llm

    client = _FakeClient(
        [
            _sse({"choices": [{"delta": {"content": "Bonjour "}}]}),
            _sse(
                {
                    "choices": [
                        {"delta": {"content": "Monsieur."}, "finish_reason": "stop"},
                    ],
                },
            ),
            _sse(
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 120,
                        "completion_tokens": 8,
                        "prompt_cache_hit_tokens": 20,
                    },
                },
            ),
            "data: [DONE]",
        ],
    )
    monkeypatch.setattr(llm.config, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm, "_get_http_client", lambda: client)
    summaries: list[dict] = []

    chunks = [
        chunk
        async for chunk in llm.chat_stream(
            [{"role": "user", "content": "Bonjour"}],
            model=llm.config.DEEPSEEK_FAST_MODEL,
            on_usage=summaries.append,
        )
    ]

    assert chunks == ["Bonjour ", "Monsieur."]
    assert client.payload is not None
    assert client.payload["stream_options"] == {"include_usage": True}
    assert summaries == [
        {
            "tokens_in": 120,
            "tokens_out": 8,
            "cache_hit": 20,
            "cost": llm.estimate_cost(llm.config.DEEPSEEK_FAST_MODEL, 120, 8, 20),
            "model": llm.config.DEEPSEEK_FAST_MODEL,
            "stop_reason": "stop",
            "usage_estimated": False,
        },
    ]


@pytest.mark.asyncio
async def test_chat_stream_marks_missing_usage_as_estimated(monkeypatch) -> None:
    import llm

    client = _FakeClient(
        [
            _sse(
                {
                    "choices": [
                        {"delta": {"content": "Oui."}, "finish_reason": "stop"},
                    ],
                },
            ),
            "data: [DONE]",
        ],
    )
    monkeypatch.setattr(llm.config, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm, "_get_http_client", lambda: client)
    summaries: list[dict] = []

    assert [
        chunk
        async for chunk in llm.chat_stream(
            [{"role": "user", "content": "Réponds brièvement"}],
            on_usage=summaries.append,
        )
    ] == ["Oui."]

    assert summaries[0]["usage_estimated"] is True
    assert summaries[0]["tokens_in"] > 0
    assert summaries[0]["tokens_out"] > 0
    assert summaries[0]["cost"] > 0


@pytest.mark.asyncio
async def test_orchestrator_forwards_stream_usage_in_done_event(monkeypatch) -> None:
    import agents.orchestrator as orchestrator_module

    orchestrator = orchestrator_module.OrchestratorAgent()
    agent = SimpleNamespace(
        name="info",
        model="model-test",
        _VALID_EMOTIONS={"neutral", "warm"},
        build_system_prompt=lambda context: "system",
        _extract_emotion=lambda content: ("neutral", content),
    )

    async def fake_classify(message: str) -> str:
        return "INFO"

    async def fake_prepare(*args, **kwargs):
        return {"history": []}, agent

    async def fake_stream(*args, on_usage, **kwargs):
        yield "Réponse suffisamment longue."
        on_usage(
            {
                "tokens_in": 42,
                "tokens_out": 7,
                "cache_hit": 2,
                "cost": 0.0042,
                "stop_reason": "stop",
                "usage_estimated": False,
            },
        )

    monkeypatch.setattr(orchestrator_module, "classify_category", fake_classify)
    monkeypatch.setattr(orchestrator, "build_context", dict)
    monkeypatch.setattr(orchestrator, "_prepare_dispatch_context", fake_prepare)
    monkeypatch.setattr(orchestrator_module.llm, "chat_stream", fake_stream)

    events = [event async for event in orchestrator.handle_stream("Bonjour")]
    done = events[-1]

    assert done["type"] == "done"
    assert done["tokens_in"] == 42
    assert done["tokens_out"] == 7
    assert done["cache_hit"] == 2
    assert done["cost"] == 0.0042
    assert done["usage_estimated"] is False
    assert done["stop_reason"] == "stop"

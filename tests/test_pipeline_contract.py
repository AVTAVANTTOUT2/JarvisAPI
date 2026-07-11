"""Tests du contrat de découplage entre main.py et les daemons."""

from __future__ import annotations

import asyncio

import pytest

import pipeline


def test_pipeline_fails_explicitly_before_configuration(monkeypatch):
    monkeypatch.setattr(pipeline, "_handlers", None)

    with pytest.raises(pipeline.PipelineNotConfiguredError):
        asyncio.run(pipeline.process_voice_fast("bonjour", 1))


def test_pipeline_delegates_all_entry_points(monkeypatch):
    calls = []

    async def process_message(text, conversation_id, voice_mode):
        calls.append(("message", text, conversation_id, voice_mode))
        return {"kind": "message"}

    async def process_voice(text, conversation_id):
        calls.append(("voice", text, conversation_id))
        return {"kind": "voice"}

    async def build_context(text, conversation_id):
        calls.append(("context", text, conversation_id))
        return {"kind": "context"}

    monkeypatch.setattr(pipeline, "_handlers", None)
    pipeline.configure_pipeline(
        process_message=process_message,
        process_voice=process_voice,
        build_context=build_context,
    )

    assert asyncio.run(pipeline.process_message_internal("a", 2, True)) == {
        "kind": "message"
    }
    assert asyncio.run(pipeline.process_voice_fast("b", 3)) == {"kind": "voice"}
    assert asyncio.run(pipeline.build_enriched_context("c", 4)) == {
        "kind": "context"
    }
    assert calls == [
        ("message", "a", 2, True),
        ("voice", "b", 3),
        ("context", "c", 4),
    ]

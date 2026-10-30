"""Allowlist et bornage des payloads task.control.* diffusés sur le bus."""

from __future__ import annotations

from jarvis.agentic.redaction import REDACTED
from jarvis.task_control.events import neutralize_task_event_payload


def test_neutralize_drops_unknown_keys_and_clamps_scalars() -> None:
    safe = neutralize_task_event_payload(
        {
            "task_id": "task-1",
            "progress": 1.7,
            "confidence": -0.2,
            "plan_version": -3,
            "deliverable_count": "4",
            "needs_attention": 1,
            "secret_blob": "should-not-cross",
            "raw_result": {"tool": "exec"},
        }
    )

    assert safe["task_id"] == "task-1"
    assert safe["progress"] == 1.0
    assert safe["confidence"] == 0.0
    assert safe["plan_version"] == 0
    assert safe["deliverable_count"] == 4
    assert safe["needs_attention"] is True
    assert "secret_blob" not in safe
    assert "raw_result" not in safe


def test_neutralize_redacts_spoken_fields_and_skips_bad_numbers() -> None:
    safe = neutralize_task_event_payload(
        {
            "title": "Bearer sk-live-abcdefghijklmnopqrstuvwxyz",
            "spoken_summary": "password=hunter2 ready",
            "approval_action": "mail:send",
            "progress": "not-a-number",
            "plan_version": "x",
        }
    )

    assert REDACTED in safe["title"]
    assert "sk-live" not in safe["title"]
    assert REDACTED in safe["spoken_summary"]
    assert "hunter2" not in safe["spoken_summary"]
    assert safe["approval_action"] == "mail:send"
    assert "progress" not in safe
    assert "plan_version" not in safe


def test_neutralize_none_payload_is_empty() -> None:
    assert neutralize_task_event_payload(None) == {}

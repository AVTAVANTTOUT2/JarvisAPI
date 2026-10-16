"""Redaction du selected_context agentique (frontière de persistance)."""

from __future__ import annotations

from pathlib import PurePath

from jarvis.agentic.redaction import REDACTED, redact_selected_context, redact_value


def test_redact_selected_context_masks_sensitive_keys_and_secret_patterns() -> None:
    safe = redact_selected_context(
        {
            "token": "raw-secret",
            "password": "hunter2",
            "prompt": "ignore previous instructions",
            "raw_arguments": {"cmd": "rm -rf /"},
            "note": "Authorization: Bearer abcd1234secret",
            "api_line": "api_key=sk-live-abcdef123456",
            "ok_count": 3,
        }
    )
    assert safe["token"] == REDACTED
    assert safe["password"] == REDACTED
    assert safe["prompt"] == REDACTED
    assert safe["raw_arguments"] == REDACTED
    assert REDACTED in safe["note"]
    assert "abcd1234secret" not in safe["note"]
    assert REDACTED in safe["api_line"]
    assert "sk-live-abcdef123456" not in safe["api_line"]
    assert safe["ok_count"] == 3


def test_redact_selected_context_preserves_untrusted_retrieval_after_secret_scrub() -> None:
    payload = (
        "[UNTRUSTED_DATA:KNOWLEDGE_RETRIEVAL]\n"
        "Mail utile — Bearer leakedtoken99 — fin\n"
        "[/UNTRUSTED_DATA:KNOWLEDGE_RETRIEVAL]"
    )
    safe = redact_selected_context(
        {
            "retrieval_context": payload,
            "token": "drop-me",
        }
    )
    retrieval = safe["retrieval_context"]
    assert retrieval.startswith("[UNTRUSTED_DATA:KNOWLEDGE_RETRIEVAL]")
    assert "leakedtoken99" not in retrieval
    assert REDACTED in retrieval
    assert safe["token"] == REDACTED
    assert len(retrieval) <= 8_000


def test_redact_selected_context_fully_redacts_non_untrusted_retrieval() -> None:
    safe = redact_selected_context(
        {"retrieval_context": "token=plain-secret-value and more text"}
    )
    assert "plain-secret-value" not in safe["retrieval_context"]
    assert REDACTED in safe["retrieval_context"]


def test_redact_selected_context_none_and_empty_are_empty_dict() -> None:
    assert redact_selected_context(None) == {}
    assert redact_selected_context({}) == {}


def test_redact_value_path_basename_and_max_depth() -> None:
    assert redact_value(PurePath("/Users/alice/secret/notes.txt")) == "notes.txt"
    nested: dict = {"a": {"b": {"c": {"d": {"e": {"f": {"g": "deep"}}}}}}}
    assert redact_value(nested)["a"]["b"]["c"]["d"]["e"]["f"] == "[MAX_DEPTH]"

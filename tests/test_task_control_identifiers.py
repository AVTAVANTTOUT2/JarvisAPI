"""Bornes des identifiants et extraits task-control (clés API / digests / UI)."""

from __future__ import annotations

import pytest

from jarvis.task_control.models import clamp_text, validate_identifier


@pytest.mark.parametrize(
    "value",
    [
        "task_abc",
        "plan.1:2",
        "A",
        "x" * 128,
        "run-1_ok:v2",
    ],
)
def test_validate_identifier_accepts_slugs(value: str) -> None:
    assert validate_identifier(value, label="id") == value.strip()


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "../x",
        "a b",
        "-leading",
        "x" * 129,
        "path/to",
        "has\nnewline",
    ],
)
def test_validate_identifier_rejects_invalid(value: str) -> None:
    with pytest.raises(ValueError, match="id invalide"):
        validate_identifier(value, label="id")


def test_validate_identifier_strips_whitespace() -> None:
    assert validate_identifier("  task_1  ", label="id") == "task_1"


def test_clamp_text_collapses_whitespace_and_truncates() -> None:
    assert clamp_text("  a   b  ", 10) == "a b"
    assert clamp_text("abcdefghij", 10) == "abcdefghij"
    assert clamp_text("abcdefghijk", 10) == "abcdefghi…"
    assert clamp_text(None, 5) == ""
    assert clamp_text(12, 5) == "12"
    assert clamp_text("ab", 0) == "…"
    assert clamp_text("ab", 1) == "…"

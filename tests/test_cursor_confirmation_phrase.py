"""Phrase exacte de confirmation Cursor — pas de faux positif sur une demande réelle."""

from __future__ import annotations

import pytest

from api.chat_cognitive import is_cursor_confirmation_phrase


@pytest.mark.parametrize(
    "text",
    [
        "lance",
        "Lance!",
        "vas-y",
        "vas y",
        "ok lance",
        "démarre",
        "demarre",
        "confirme",
        "go",
        "  lance  ",
        "lance.",
    ],
)
def test_cursor_confirmation_phrases_accepted(text: str) -> None:
    assert is_cursor_confirmation_phrase(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "lance les tests",
        "confirme le plan",
        "lance pas",
        "démarrer",
        "vas-y demain",
        "ok",
        "lance\nlance",
        "please lance",
    ],
)
def test_cursor_confirmation_phrases_rejected(text: str) -> None:
    assert is_cursor_confirmation_phrase(text) is False


def test_cursor_confirmation_handles_none() -> None:
    assert is_cursor_confirmation_phrase(None) is False  # type: ignore[arg-type]

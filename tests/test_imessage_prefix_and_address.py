"""Filtre préfixe iMessage et validation d'adresse (pures, hors osascript)."""

from __future__ import annotations

import pytest

from integrations.imessage import IMessageBridge, validate_imessage_address
from integrations._applescript import escape_applescript_string


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("+33612345678", "+33612345678"),
        ("  user@example.com  ", "user@example.com"),
        ("", None),
        ("not-an-address", None),
        ("a" * 255, None),
        ("+33 612", None),
    ],
)
def test_validate_imessage_address(address: str, expected: str | None) -> None:
    assert validate_imessage_address(address) == expected


def test_apply_prefix_filter_strips_configured_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.IMESSAGE_PREFIX", "jarvis")
    bridge = IMessageBridge("+33600000000")
    assert bridge._apply_prefix_filter("jarvis quel temps ?") == "quel temps ?"
    assert bridge._apply_prefix_filter("JARVIS: mail urgent") == "mail urgent"
    assert bridge._apply_prefix_filter("bonjour") is None


def test_apply_prefix_filter_passthrough_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.IMESSAGE_PREFIX", "")
    bridge = IMessageBridge("+33600000000")
    assert bridge._apply_prefix_filter("tout passe") == "tout passe"


def test_escape_applescript_string_order() -> None:
    # Antislash avant guillemet : sinon `\"` devient `\\"`.
    assert escape_applescript_string('a\\b"c\nd') == 'a\\\\b\\"c\\nd'
    assert escape_applescript_string("") == ""
    assert escape_applescript_string(None) == ""  # type: ignore[arg-type]

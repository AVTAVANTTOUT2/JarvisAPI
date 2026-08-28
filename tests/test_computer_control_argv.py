"""Frontière argv de `ComputerControl` — `open_app` s'exécute sans confirmation.

`open_app` n'appartient pas au flux de confirmation : un bloc ```action```
produit par le LLM sous injection de prompt le déclenche directement. La seule
barrière est donc `_validate_argv`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.computer import _OPEN, ComputerControl


@pytest.fixture
def computer() -> ComputerControl:
    control = ComputerControl()
    control.allowed = True
    return control


@pytest.fixture(autouse=True)
def _no_app_allowlist(monkeypatch: pytest.MonkeyPatch):
    """Par défaut : aucune restriction, comme le comportement historique."""
    monkeypatch.setattr("config.COMPUTER_ALLOWED_APPS", frozenset())


@pytest.mark.parametrize(
    "name",
    [
        "/Applications/Evil.app",
        "../../Users/example/Downloads/Evil.app",
        "Safari/../Evil",
        "C:\\Evil.app",
        "Sa\nfari",
        "Safari\x00",
        "",
        "A" * 129,
    ],
)
def test_open_app_rejects_paths_and_control_characters(
    computer: ComputerControl, name: str
):
    """Un bundle arbitraire sur disque ne doit pas être lançable par chemin."""
    ok, reason = computer._validate_argv((_OPEN, "-a", name))
    assert ok is False
    assert reason == "nom d'application invalide"


def test_open_app_accepts_a_plain_registered_name(computer: ComputerControl):
    ok, _ = computer._validate_argv((_OPEN, "-a", "Safari"))
    assert ok is True


def test_empty_allowlist_keeps_historic_behaviour(computer: ComputerControl):
    for name in ("Safari", "Terminal", "Réglages Système"):
        ok, _ = computer._validate_argv((_OPEN, "-a", name))
        assert ok is True, name


def test_configured_allowlist_is_strict(
    computer: ComputerControl, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "config.COMPUTER_ALLOWED_APPS", frozenset({"safari", "notes"})
    )

    ok, _ = computer._validate_argv((_OPEN, "-a", "Safari"))
    assert ok is True

    ok, reason = computer._validate_argv((_OPEN, "-a", "Terminal"))
    assert ok is False
    assert reason == "application hors de COMPUTER_ALLOWED_APPS"


def test_allowlist_is_case_and_whitespace_insensitive(
    computer: ComputerControl, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("config.COMPUTER_ALLOWED_APPS", frozenset({"safari"}))
    for variant in ("safari", "Safari", "SAFARI", "  Safari  "):
        ok, _ = computer._validate_argv((_OPEN, "-a", variant))
        assert ok is True, variant


def test_allowlist_does_not_loosen_the_path_check(
    computer: ComputerControl, monkeypatch: pytest.MonkeyPatch
):
    """Une entrée d'allowlist contenant un chemin ne doit rien débloquer."""
    monkeypatch.setattr(
        "config.COMPUTER_ALLOWED_APPS", frozenset({"/applications/evil.app"})
    )
    ok, reason = computer._validate_argv((_OPEN, "-a", "/Applications/Evil.app"))
    assert ok is False
    assert reason == "nom d'application invalide"


@pytest.mark.parametrize(
    "argv",
    [
        (_OPEN, "-a"),
        (_OPEN, "Safari"),
        (_OPEN, "-e", "Safari"),
        (_OPEN, "-a", "Safari", "--args", "whatever"),
        (_OPEN, "javascript:alert(1)"),
        (_OPEN, "-a", "Safari", "javascript:alert(1)"),
    ],
)
def test_forbidden_open_forms_are_rejected(
    computer: ComputerControl, argv: tuple[str, ...]
):
    """``open --args``, schémas dangereux et formes incomplètes restent hors allowlist."""
    ok, _ = computer._validate_argv(argv)
    assert ok is False


def test_open_url_and_open_app_with_url_are_accepted(computer: ComputerControl):
    url = "https://www.youtube.com/@Squeezie"
    ok, reason = computer._validate_argv((_OPEN, url))
    assert ok is True, reason
    ok, reason = computer._validate_argv((_OPEN, "-a", "Safari", url))
    assert ok is True, reason


def test_open_path_outside_home_is_rejected(
    computer: ComputerControl, tmp_path: Path
):
    computer.home = str(tmp_path)
    ok, reason = computer._validate_argv((_OPEN, "/etc/passwd"))
    assert ok is False
    assert reason == "chemin hors du home"


@pytest.mark.asyncio
async def test_open_app_is_inert_without_computer_access(
    monkeypatch: pytest.MonkeyPatch,
):
    control = ComputerControl()
    control.allowed = False
    result = await control.open_app("Safari")
    assert result["ok"] is False

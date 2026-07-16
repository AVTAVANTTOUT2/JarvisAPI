"""Calendar ne doit jamais être ramené au premier plan par JARVIS."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _arg_token(elt: ast.AST) -> str | None:
    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
        return elt.value
    if isinstance(elt, ast.Name) and elt.id == "CALENDAR_APP_ID":
        return "com.apple.iCal"
    return None


def _open_calendar_calls(path: Path) -> list[tuple[int, list[str]]]:
    """Retourne les appels subprocess open visant Calendar (ligne + argv)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, list[str]]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        args = node.args
        if not args:
            continue
        first = args[0]
        if not isinstance(first, (ast.List, ast.Tuple)):
            continue
        parts: list[str] = []
        calendarish = False
        for elt in first.elts:
            token = _arg_token(elt)
            if token is None:
                parts.append("<dyn>")
                continue
            parts.append(token)
            if token in ("Calendar", "com.apple.iCal") or "Calendar" in token:
                calendarish = True
        if not parts or parts[0] != "open" or not calendarish:
            continue
        hits.append((node.lineno, parts))
    return hits


def test_lifespan_does_not_open_calendar_at_startup():
    """Startup ne doit plus du tout appeler `open` sur Calendar (anti plein-écran)."""
    hits = _open_calendar_calls(ROOT / "api" / "lifespan.py")
    assert hits == [], f"lifespan ne doit plus ouvrir Calendar : {hits}"
    text = (ROOT / "api" / "lifespan.py").read_text(encoding="utf-8")
    assert "open -a Calendar" not in text
    assert '["open", "-a", "Calendar"]' not in text
    assert "pas de lancement préventif" in text


def test_calendar_api_open_only_background_and_write_path():
    """Tout open restant dans calendar_api doit être -gj (écriture explicite)."""
    hits = _open_calendar_calls(ROOT / "integrations" / "calendar_api.py")
    assert hits, "calendar_api doit garder open -gj pour create_event uniquement"
    for lineno, parts in hits:
        assert "-gj" in parts or ("-g" in parts and "-j" in parts), (
            f"integrations/calendar_api.py:{lineno} open non silencieux : {parts}"
        )
        assert "com.apple.iCal" in parts


def test_no_bare_open_a_calendar_anywhere():
    """Aucun `open -a Calendar` sans -g/-j dans le code applicatif."""
    offenders: list[str] = []
    for path in [
        ROOT / "api" / "lifespan.py",
        ROOT / "integrations" / "calendar_api.py",
        ROOT / "scripts" / "jarvis_daemon.py",
        ROOT / "actions.py",
    ]:
        if not path.exists():
            continue
        for lineno, parts in _open_calendar_calls(path):
            has_bg = "-gj" in parts or "-g" in parts
            if "-a" in parts and "Calendar" in parts and not has_bg:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno} → {parts}")
    assert offenders == []


def test_launch_calendar_never_opens_process():
    """Lecture / is_available : pas de lancement si Calendar est éteint."""
    from integrations.calendar_api import AppleCalendarClient

    client = AppleCalendarClient()
    with patch.object(client, "_calendar_process_running", return_value=False):
        with patch.object(client, "_open_calendar_background") as mock_open:
            diag = client._launch_calendar()
            mock_open.assert_not_called()
    assert diag["ok"] is False
    assert diag["reason"] == "not_running"


def test_calendar_reminder_disabled_by_default():
    import config

    assert config.CALENDAR_REMINDER_ENABLED is False


def test_restore_focus_hides_calendar_when_it_stole_front():
    from integrations.calendar_api import AppleCalendarClient
    from integrations._applescript import OsascriptResult

    client = AppleCalendarClient()
    calls: list[str] = []

    def fake_run(script: str, **kwargs):
        calls.append(script)
        if "set visible of process" in script:
            return OsascriptResult(True, "ok", 0, "", "")
        return OsascriptResult(True, "ok", 0, "Calendar", "")

    with patch.object(client, "_frontmost_process_name", return_value="Calendar"):
        with patch("integrations.calendar_api.run_applescript", side_effect=fake_run):
            client._restore_focus_if_calendar_stole("Firefox")

    assert any("set visible of process" in s for s in calls)


def test_restore_focus_noop_if_user_was_already_in_calendar():
    from integrations.calendar_api import AppleCalendarClient

    client = AppleCalendarClient()
    with patch("integrations.calendar_api.run_applescript") as mock_run:
        client._restore_focus_if_calendar_stole("Calendar")
        mock_run.assert_not_called()

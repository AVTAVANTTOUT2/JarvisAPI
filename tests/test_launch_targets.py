"""Résolution des cibles launch — URL, schéma, fichier $HOME, YouTube handle."""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.launch_targets import (
    resolve_launch_target,
)


@pytest.fixture
def home(tmp_path: Path) -> str:
    return str(tmp_path)


def test_youtube_url_is_accepted(home: str):
    spec, error = resolve_launch_target(
        url="https://www.youtube.com/@Squeezie",
        home=home,
    )
    assert error == ""
    assert spec is not None
    assert spec.kind == "url"
    assert spec.target == "https://www.youtube.com/@Squeezie"
    assert spec.app is None


def test_youtube_handle_with_app_expands(home: str):
    spec, error = resolve_launch_target(
        query="Squeezie",
        app="YouTube",
        home=home,
    )
    assert error == ""
    assert spec is not None
    assert spec.target == "https://www.youtube.com/@Squeezie"
    assert spec.app == "YouTube"


def test_bare_handle_without_youtube_hint_is_ambiguous(home: str):
    spec, error = resolve_launch_target(query="Squeezie", home=home)
    assert spec is None
    assert error == "cible ambiguë"


def test_javascript_url_is_rejected(home: str):
    spec, error = resolve_launch_target(url="javascript:alert(1)", home=home)
    assert spec is None
    assert error == "schéma interdit"


def test_credentials_in_url_are_rejected(home: str):
    spec, error = resolve_launch_target(
        url="https://user:pass@example.com/secret",
        home=home,
    )
    assert spec is None
    assert "identifiants" in error


def test_file_url_outside_home_is_rejected(home: str):
    spec, error = resolve_launch_target(url="file:///etc/passwd", home=home)
    assert spec is None
    assert error == "chemin hors du home"


def test_file_url_percent_encoded_traversal_is_rejected(home: str):
    escaped = f"file://{home}/%2e%2e/%2e%2e/etc/passwd"
    spec, error = resolve_launch_target(url=escaped, home=home)
    assert spec is None
    assert error == "chemin hors du home"


def test_file_url_double_encoded_traversal_is_rejected(home: str):
    escaped = f"file://{home}/%252e%252e/%252e%252e/etc/passwd"
    spec, error = resolve_launch_target(url=escaped, home=home)
    assert spec is None
    assert error == "chemin hors du home"


def test_shortcuts_scheme_is_rejected(home: str):
    spec, error = resolve_launch_target(
        url="shortcuts://run-shortcut?name=UnregisteredShortcut",
        home=home,
    )
    assert spec is None
    assert "raccourcis" in error


def test_path_inside_home_is_accepted(home: str, tmp_path: Path):
    target = tmp_path / "rapport.pdf"
    target.write_text("x", encoding="utf-8")
    spec, error = resolve_launch_target(path=str(target), home=home)
    assert error == ""
    assert spec is not None
    assert spec.kind == "path"
    assert spec.target == str(target.resolve())


def test_app_name_only(home: str):
    spec, error = resolve_launch_target(name="Notes", home=home)
    assert error == ""
    assert spec is not None
    assert spec.kind == "app"
    assert spec.target == "Notes"
    assert spec.argv("/usr/bin/open") == ("/usr/bin/open", "-a", "Notes")


def test_missing_target(home: str):
    spec, error = resolve_launch_target(home=home)
    assert spec is None
    assert error == "cible manquante"

"""Résolution des cibles launch — URL, schéma, fichier $HOME, YouTube handle."""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.launch_targets import (
    MAX_LAUNCH_TARGET_CHARS,
    is_allowed_home_path,
    is_allowed_launch_url,
    is_safe_app_name,
    open_target_allowed,
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


@pytest.mark.parametrize(
    "handle",
    [
        "../etc/passwd",
        "bad handle",
        "@@@",
        "a" * 65,
    ],
)
def test_invalid_youtube_handle_is_rejected(home: str, handle: str):
    by_query, query_error = resolve_launch_target(
        query=handle, app="YouTube", home=home
    )
    assert by_query is None
    assert query_error == "identifiant youtube invalide"

    by_url, url_error = resolve_launch_target(url=handle, app="YouTube", home=home)
    assert by_url is None
    assert url_error == "identifiant youtube invalide"


def test_bare_handle_without_youtube_hint_is_ambiguous(home: str):
    spec, error = resolve_launch_target(query="Squeezie", home=home)
    assert spec is None
    assert error == "cible ambiguë"


def test_javascript_url_is_rejected(home: str):
    spec, error = resolve_launch_target(url="javascript:alert(1)", home=home)
    assert spec is None
    assert error == "schéma interdit"


@pytest.mark.parametrize(
    "url",
    [
        "data:text/html,<script>alert(1)</script>",
        "smb://fileserver/share",
        "ftp://example.com/file",
        "ssh://host/path",
    ],
)
def test_blocked_schemes_are_rejected(home: str, url: str):
    spec, error = resolve_launch_target(url=url, home=home)
    assert spec is None
    assert error == "schéma interdit"
    assert is_allowed_launch_url(url, home=home) == (False, "schéma interdit")


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


def test_file_url_inside_home_is_accepted(home: str, tmp_path: Path):
    target = tmp_path / "notes.txt"
    target.write_text("ok", encoding="utf-8")
    url = target.resolve().as_uri()
    spec, error = resolve_launch_target(url=url, home=home)
    assert error == ""
    assert spec is not None
    assert spec.kind == "url"
    assert spec.target == url


def test_path_inside_home_is_accepted(home: str, tmp_path: Path):
    target = tmp_path / "rapport.pdf"
    target.write_text("x", encoding="utf-8")
    spec, error = resolve_launch_target(path=str(target), home=home)
    assert error == ""
    assert spec is not None
    assert spec.kind == "path"
    assert spec.target == str(target.resolve())


def test_relative_path_is_rejected(home: str):
    ok, detail = is_allowed_home_path("Documents/secret.pdf", home=home)
    assert ok is False
    assert detail == "chemin relatif interdit"
    spec, error = resolve_launch_target(path="Documents/secret.pdf", home=home)
    assert spec is None
    assert error == "chemin relatif interdit"


def test_oversized_url_is_rejected(home: str):
    url = "https://example.com/" + ("a" * (MAX_LAUNCH_TARGET_CHARS + 1))
    ok, detail = is_allowed_launch_url(url, home=home)
    assert ok is False
    assert detail == "cible trop longue"


def test_control_characters_in_url_are_rejected(home: str):
    ok, detail = is_allowed_launch_url("https://example.com/a\x00b", home=home)
    assert ok is False
    assert detail == "url invalide"


def test_scheme_without_host_marker_is_rejected(home: str):
    """Une cible sans schéma exploitable ne doit pas être inventée."""
    spec, error = resolve_launch_target(url="not a url", home=home)
    assert spec is None
    assert error in {"schéma manquant", "schéma interdit"}


def test_unsafe_app_names_are_rejected(home: str):
    assert is_safe_app_name("Safari") is True
    assert is_safe_app_name("../Evil") is False
    assert is_safe_app_name("App\nName") is False
    assert is_safe_app_name("") is False
    spec, error = resolve_launch_target(name="Apps/Notes", home=home)
    assert spec is None
    assert error == "nom d'application invalide"


def test_open_target_allowed_routes_urls_and_home_paths(home: str, tmp_path: Path):
    target = tmp_path / "doc.txt"
    target.write_text("x", encoding="utf-8")
    assert open_target_allowed(str(target), home=home)[0] is True
    assert open_target_allowed("https://example.com", home=home) == (True, "https://example.com")
    assert open_target_allowed("javascript:alert(1)", home=home) == (
        False,
        "schéma interdit",
    )
    assert open_target_allowed("/etc/passwd", home=home) == (False, "chemin hors du home")


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

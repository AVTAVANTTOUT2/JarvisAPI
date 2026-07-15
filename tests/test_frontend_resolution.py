"""Tests de résolution frontend desktop (core.frontend_resolution)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.frontend_resolution import (
    lookup_desktop_static_file,
    resolve_desktop_frontend,
)


def _make_next(root: Path) -> Path:
    out = root / "frontend" / "out"
    (out / "_next" / "static" / "chunks").mkdir(parents=True)
    (out / "chat").mkdir(parents=True)
    (out / "dashboard").mkdir(parents=True)
    (out / "index.html").write_text("<html>next-root</html>", encoding="utf-8")
    (out / "chat" / "index.html").write_text("<html>next-chat</html>", encoding="utf-8")
    (out / "dashboard" / "index.html").write_text("<html>next-dash</html>", encoding="utf-8")
    (out / "_next" / "static" / "chunks" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return out


def _make_vite(root: Path) -> Path:
    dist = root / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>vite-root</html>", encoding="utf-8")
    (dist / "assets" / "index.js").write_text("console.log('vite')", encoding="utf-8")
    return dist


def test_both_builds_selects_next(tmp_path: Path) -> None:
    _make_next(tmp_path)
    _make_vite(tmp_path)
    res = resolve_desktop_frontend(tmp_path)
    assert res.kind == "next_canonical"
    assert res.relative_path == "frontend/out"
    assert res.canonical_available is True
    assert res.fallback_available is True


def test_next_absent_vite_present(tmp_path: Path) -> None:
    _make_vite(tmp_path)
    res = resolve_desktop_frontend(tmp_path)
    assert res.kind == "vite_fallback"
    assert res.relative_path == "web/dist"
    assert "fallback" in res.reason.lower() or "missing" in res.reason.lower()


def test_next_empty_vite_valid(tmp_path: Path) -> None:
    (tmp_path / "frontend" / "out").mkdir(parents=True)
    _make_vite(tmp_path)
    res = resolve_desktop_frontend(tmp_path)
    assert res.kind == "vite_fallback"
    assert res.canonical_available is False


def test_next_valid_vite_absent(tmp_path: Path) -> None:
    _make_next(tmp_path)
    res = resolve_desktop_frontend(tmp_path)
    assert res.kind == "next_canonical"
    assert res.fallback_available is False


def test_none_missing(tmp_path: Path) -> None:
    res = resolve_desktop_frontend(tmp_path)
    assert res.kind == "missing"
    assert res.root is None
    assert res.checked == ("frontend/out", "web/dist")


def test_resolution_independent_of_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_next(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    assert Path.cwd() == other.resolve()
    res = resolve_desktop_frontend(tmp_path)
    assert res.kind == "next_canonical"
    assert res.root is not None
    assert res.root.name == "out"


def test_lookup_next_routes(tmp_path: Path) -> None:
    _make_next(tmp_path)
    res = resolve_desktop_frontend(tmp_path)
    root = lookup_desktop_static_file(res, "")
    assert root is not None and root.read_text(encoding="utf-8") == "<html>next-root</html>"
    chat = lookup_desktop_static_file(res, "chat")
    assert chat is not None and "next-chat" in chat.read_text(encoding="utf-8")
    chat_slash = lookup_desktop_static_file(res, "chat/")
    assert chat_slash is not None
    asset = lookup_desktop_static_file(res, "_next/static/chunks/app.js")
    assert asset is not None
    missing_asset = lookup_desktop_static_file(res, "_next/static/missing.js")
    assert missing_asset is None
    unknown = lookup_desktop_static_file(res, "not-a-route")
    assert unknown is None


def test_lookup_vite_spa_fallback(tmp_path: Path) -> None:
    _make_vite(tmp_path)
    res = resolve_desktop_frontend(tmp_path)
    assert lookup_desktop_static_file(res, "chat") is not None
    assert lookup_desktop_static_file(res, "assets/missing.js") is None

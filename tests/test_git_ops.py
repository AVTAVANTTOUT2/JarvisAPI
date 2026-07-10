"""Tests du rebase Git sûr (résolution triviale uniquement, abort sinon)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ── Unité : _resolve_trivial_conflicts (texte pur, pas de git réel) ──────

def test_resolve_identical_sides():
    from agents.devagent.git_ops import _resolve_trivial_conflicts

    text = "A\n<<<<<<< HEAD\nsame\n=======\nsame\n>>>>>>> feature\nB\n"
    resolved, ok = _resolve_trivial_conflicts(text)
    assert ok is True
    assert "<<<<<<<" not in resolved
    assert resolved == "A\nsame\nB\n"


def test_resolve_ours_empty_keeps_theirs():
    from agents.devagent.git_ops import _resolve_trivial_conflicts

    text = "A\n<<<<<<< HEAD\n=======\ntheirs-content\n>>>>>>> feature\nB\n"
    resolved, ok = _resolve_trivial_conflicts(text)
    assert ok is True
    assert resolved == "A\ntheirs-content\nB\n"


def test_resolve_theirs_empty_keeps_ours():
    from agents.devagent.git_ops import _resolve_trivial_conflicts

    text = "A\n<<<<<<< HEAD\nours-content\n=======\n>>>>>>> feature\nB\n"
    resolved, ok = _resolve_trivial_conflicts(text)
    assert ok is True
    assert resolved == "A\nours-content\nB\n"


def test_resolve_non_trivial_leaves_markers():
    from agents.devagent.git_ops import _resolve_trivial_conflicts

    text = "A\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> feature\nB\n"
    resolved, ok = _resolve_trivial_conflicts(text)
    assert ok is False
    assert "<<<<<<<" in resolved  # inchangé


def test_resolve_multiple_blocks_one_non_trivial():
    from agents.devagent.git_ops import _resolve_trivial_conflicts

    text = (
        "<<<<<<< HEAD\nsame\n=======\nsame\n>>>>>>> f\n"
        "middle\n"
        "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> f\n"
    )
    resolved, ok = _resolve_trivial_conflicts(text)
    assert ok is False  # un seul bloc non trivial suffit à tout invalider


# ── Intégration : vrais dépôts git, vrais conflits ───────────────────────

def _git(args, cwd):
    from agents.devagent.executor import run_isolated
    return run_isolated(["git", *args], cwd=cwd, timeout=15)


def _init_repo_with_base(tmp_path: Path, content: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    (repo / "f.txt").write_text(content, encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "base"], repo)
    return repo


def test_safe_rebase_resolves_modify_delete_conflict(tmp_path):
    """Suppression (feature) vs modification (main) sur la même ligne → trivial."""
    from agents.devagent.git_ops import safe_rebase

    repo = _init_repo_with_base(tmp_path, "A\nB\nC\nD\nE\n")
    _git(["checkout", "-q", "-b", "feature"], repo)
    (repo / "f.txt").write_text("A\nB\nD\nE\n", encoding="utf-8")  # supprime C
    _git(["commit", "-q", "-am", "feature: remove C"], repo)

    _git(["checkout", "-q", "main"], repo)
    (repo / "f.txt").write_text("A\nB\nC-edited\nD\nE\n", encoding="utf-8")
    _git(["commit", "-q", "-am", "main: edit C"], repo)

    _git(["checkout", "-q", "feature"], repo)
    result = safe_rebase(repo, onto="main")

    assert result["ok"] is True
    assert result["resolved_trivial"] is True
    assert "f.txt" in result["files_resolved"]
    assert (repo / "f.txt").read_text(encoding="utf-8") == "A\nB\nC-edited\nD\nE\n"
    # rebase terminé proprement, pas de conflit en attente
    status = _git(["status", "--short"], repo)
    assert status["stdout"].strip() == ""


def test_safe_rebase_aborts_on_real_conflict(tmp_path):
    """Deux modifications différentes de la même ligne → non trivial → abort total."""
    from agents.devagent.git_ops import safe_rebase

    repo = _init_repo_with_base(tmp_path, "A\nB\nC\nD\nE\n")
    _git(["checkout", "-q", "-b", "feature"], repo)
    (repo / "f.txt").write_text("A\nB\nC-feature\nD\nE\n", encoding="utf-8")
    _git(["commit", "-q", "-am", "feature: edit C differently"], repo)
    feature_head = _git(["rev-parse", "HEAD"], repo)["stdout"].strip()

    _git(["checkout", "-q", "main"], repo)
    (repo / "f.txt").write_text("A\nB\nC-main\nD\nE\n", encoding="utf-8")
    _git(["commit", "-q", "-am", "main: edit C differently"], repo)

    _git(["checkout", "-q", "feature"], repo)
    result = safe_rebase(repo, onto="main")

    assert result["ok"] is False
    assert result["resolved_trivial"] is False
    assert "f.txt" in result["files_needing_manual_review"]

    # le dépôt est revenu EXACTEMENT à son état d'avant tentative
    status = _git(["status", "--short"], repo)
    assert status["stdout"].strip() == ""
    assert _git(["rev-parse", "HEAD"], repo)["stdout"].strip() == feature_head
    assert (repo / "f.txt").read_text(encoding="utf-8") == "A\nB\nC-feature\nD\nE\n"


def test_safe_rebase_clean_when_no_conflict(tmp_path):
    """Changements sur des fichiers différents → rebase propre, pas de résolution nécessaire."""
    from agents.devagent.git_ops import safe_rebase

    repo = _init_repo_with_base(tmp_path, "A\nB\nC\n")
    _git(["checkout", "-q", "-b", "feature"], repo)
    (repo / "other.txt").write_text("feature file\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "feature: add other file"], repo)

    _git(["checkout", "-q", "main"], repo)
    (repo / "unrelated.txt").write_text("main file\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "main: add unrelated file"], repo)

    _git(["checkout", "-q", "feature"], repo)
    result = safe_rebase(repo, onto="main")

    assert result == {"ok": True, "resolved_trivial": False, "message": "rebase propre, aucun conflit"}
    assert (repo / "unrelated.txt").exists() and (repo / "other.txt").exists()

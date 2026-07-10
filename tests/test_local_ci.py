"""Tests de la CI locale (lint/tests/build) et de l'installeur de hook git."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


# ── step_lint ────────────────────────────────────────────────

def test_lint_py_compile_fallback_detects_syntax_error(tmp_path, monkeypatch):
    from scripts.local_ci import step_lint

    monkeypatch.setattr("shutil.which", lambda name: None)  # simule ruff absent
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    result = step_lint(tmp_path, files=[tmp_path / "bad.py"])
    assert result["ok"] is False
    assert "ruff absent" in result["tool"]


def test_lint_py_compile_fallback_passes_on_valid_syntax(tmp_path, monkeypatch):
    from scripts.local_ci import step_lint

    monkeypatch.setattr("shutil.which", lambda name: None)
    (tmp_path / "good.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    result = step_lint(tmp_path, files=[tmp_path / "good.py"])
    assert result["ok"] is True
    assert result["files_checked"] == 1


# ── step_tests ───────────────────────────────────────────────

def test_step_tests_ok_field_reflects_returncode(tmp_db, tmp_path, monkeypatch):
    from scripts.local_ci import step_tests

    def fake_run(cmd, cwd, timeout=300):
        return {"returncode": 0, "stdout": "1 passed", "stderr": "", "duration_ms": 42.0}

    monkeypatch.setattr("scripts.local_ci._run", fake_run)
    result = step_tests(tmp_path)
    assert result["ok"] is True
    assert result["duration_ms"] == 42.0

    from database import get_perf_history
    assert len(get_perf_history("jarvis")) == 1  # benchmark bien enregistré


def test_step_tests_failure_reported(tmp_db, tmp_path, monkeypatch):
    from scripts.local_ci import step_tests

    def fake_run(cmd, cwd, timeout=300):
        return {"returncode": 1, "stdout": "", "stderr": "1 failed", "duration_ms": 10.0}

    monkeypatch.setattr("scripts.local_ci._run", fake_run)
    result = step_tests(tmp_path)
    assert result["ok"] is False
    assert "1 failed" in result["output"]


# ── step_frontend_build ──────────────────────────────────────

def test_frontend_build_skipped_by_default(tmp_path, monkeypatch):
    from scripts.local_ci import step_frontend_build

    monkeypatch.setattr("config.LOCAL_CI_RUN_FRONTEND_BUILD", False)
    assert step_frontend_build(tmp_path) is None


def test_frontend_build_skips_without_pnpm_or_web_dir(tmp_path, monkeypatch):
    from scripts.local_ci import step_frontend_build

    monkeypatch.setattr("config.LOCAL_CI_RUN_FRONTEND_BUILD", True)
    result = step_frontend_build(tmp_path)  # pas de web/package.json ici
    assert result["ok"] is True
    assert "skip" in result["output"]


# ── run_local_ci (orchestration) ──────────────────────────────

def test_run_local_ci_all_ok_when_steps_pass(tmp_path, monkeypatch, tmp_db):
    from scripts.local_ci import run_local_ci

    monkeypatch.setattr(
        "scripts.local_ci.step_lint", lambda root, files=None: {"name": "lint", "ok": True, "duration_ms": 1}
    )
    monkeypatch.setattr(
        "scripts.local_ci.step_tests", lambda root: {"name": "tests", "ok": True, "duration_ms": 1}
    )
    monkeypatch.setattr("scripts.local_ci.step_frontend_build", lambda root: None)

    report = run_local_ci(tmp_path)
    assert report["all_ok"] is True
    assert len(report["steps"]) == 2


def test_run_local_ci_fails_if_any_step_fails(tmp_path, monkeypatch, tmp_db):
    from scripts.local_ci import run_local_ci

    monkeypatch.setattr(
        "scripts.local_ci.step_lint", lambda root, files=None: {"name": "lint", "ok": False, "duration_ms": 1}
    )
    monkeypatch.setattr(
        "scripts.local_ci.step_tests", lambda root: {"name": "tests", "ok": True, "duration_ms": 1}
    )
    monkeypatch.setattr("scripts.local_ci.step_frontend_build", lambda root: None)

    report = run_local_ci(tmp_path)
    assert report["all_ok"] is False


# ── Installeur de hook ────────────────────────────────────────

def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def test_install_hook_creates_executable_pre_commit(tmp_path, monkeypatch):
    import scripts.install_git_hooks as igh

    _init_git_repo(tmp_path)
    monkeypatch.setattr(igh, "BASE_DIR", tmp_path)

    result = igh.install()
    assert result["ok"] is True
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    assert hook.exists()
    assert hook.stat().st_mode & 0o111  # exécutable
    assert igh.HOOK_MARKER in hook.read_text(encoding="utf-8")


def test_install_hook_refuses_to_overwrite_foreign_hook(tmp_path, monkeypatch):
    import scripts.install_git_hooks as igh

    _init_git_repo(tmp_path)
    monkeypatch.setattr(igh, "BASE_DIR", tmp_path)
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'hook tiers'\n", encoding="utf-8")

    result = igh.install()
    assert result["ok"] is False
    assert "tiers" in result["reason"]

    forced = igh.install(force=True)
    assert forced["ok"] is True


def test_install_hook_is_idempotent(tmp_path, monkeypatch):
    import scripts.install_git_hooks as igh

    _init_git_repo(tmp_path)
    monkeypatch.setattr(igh, "BASE_DIR", tmp_path)

    first = igh.install()
    second = igh.install()  # notre propre hook déjà en place → pas de refus
    assert first["ok"] is True and second["ok"] is True


def test_uninstall_removes_our_hook_only(tmp_path, monkeypatch):
    import scripts.install_git_hooks as igh

    _init_git_repo(tmp_path)
    monkeypatch.setattr(igh, "BASE_DIR", tmp_path)
    igh.install()

    result = igh.uninstall()
    assert result == {"ok": True, "removed": True}
    assert not (tmp_path / ".git" / "hooks" / "pre-commit").exists()


def test_uninstall_refuses_foreign_hook(tmp_path, monkeypatch):
    import scripts.install_git_hooks as igh

    _init_git_repo(tmp_path)
    monkeypatch.setattr(igh, "BASE_DIR", tmp_path)
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho tiers\n", encoding="utf-8")

    result = igh.uninstall()
    assert result["ok"] is False
    assert hook.exists()


def test_install_fails_outside_git_repo(tmp_path, monkeypatch):
    import scripts.install_git_hooks as igh

    monkeypatch.setattr(igh, "BASE_DIR", tmp_path)  # pas de .git ici
    result = igh.install()
    assert result["ok"] is False

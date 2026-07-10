"""Tests du self-healing (diagnostic, patch réversible, rollback, cooldown)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

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


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    """Dépôt git réel avec un fichier .py suivi, et le state file redirigé dans tmp_path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "buggy.py").write_text("def f():\n    return 1 / 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    monkeypatch.setattr("scripts.self_healing.STATE_PATH", tmp_path / ".self_healing_state.json")
    return repo


def _commit_count(repo: Path) -> int:
    result = subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True)
    return len(result.stdout.strip().splitlines())


# ── Garde-fous de haut niveau ─────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_by_default(tmp_db, tmp_repo, monkeypatch):
    from scripts.self_healing import handle_crash_loop

    monkeypatch.setattr("config.SELF_HEALING_ENABLED", False)
    result = await handle_crash_loop("Traceback: boom", root=tmp_repo)
    assert result == {"ok": False, "reason": "SELF_HEALING_ENABLED désactivé"}


@pytest.mark.asyncio
async def test_diagnose_only_when_auto_apply_disabled(tmp_db, tmp_repo, monkeypatch):
    from database import get_unread_notifications
    from scripts.self_healing import handle_crash_loop

    monkeypatch.setattr("config.SELF_HEALING_ENABLED", True)
    monkeypatch.setattr("config.SELF_HEALING_AUTO_APPLY", False)

    fake_diagnosis = {
        "root_cause": "Division par zéro dans buggy.py", "confidence": "high",
        "file": "buggy.py", "fix_content": "def f():\n    return 0\n",
    }
    with patch("scripts.self_healing.diagnose_crash", new=AsyncMock(return_value=fake_diagnosis)):
        result = await handle_crash_loop("Traceback: ZeroDivisionError", root=tmp_repo)

    assert result["action"] == "diagnosed_only"
    assert _commit_count(tmp_repo) == 1  # aucun commit ajouté
    notifs = [n for n in get_unread_notifications(10) if "Diagnostic self-healing" in n["title"]]
    assert len(notifs) == 1
    assert "Division par zéro" in notifs[0]["content"]


# ── Application du patch ──────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_apply_success_commits_patch(tmp_db, tmp_repo, monkeypatch):
    from scripts.self_healing import _load_state, handle_crash_loop

    monkeypatch.setattr("config.SELF_HEALING_ENABLED", True)
    monkeypatch.setattr("config.SELF_HEALING_AUTO_APPLY", True)

    fake_diagnosis = {
        "root_cause": "Division par zéro", "confidence": "high",
        "file": "buggy.py", "fix_content": "def f():\n    return 0\n",
    }
    with patch("scripts.self_healing.diagnose_crash", new=AsyncMock(return_value=fake_diagnosis)):
        result = await handle_crash_loop("Traceback: ZeroDivisionError", root=tmp_repo)

    assert result["action"] == "patched"
    assert _commit_count(tmp_repo) == 2
    assert (tmp_repo / "buggy.py").read_text(encoding="utf-8") == "def f():\n    return 0\n"

    state = _load_state()
    assert state["last_patch_commit"] == result["commit_sha"]
    assert state["last_patch_at"] is not None


@pytest.mark.asyncio
async def test_auto_apply_rejects_non_compiling_fix(tmp_db, tmp_repo, monkeypatch):
    from scripts.self_healing import handle_crash_loop

    monkeypatch.setattr("config.SELF_HEALING_ENABLED", True)
    monkeypatch.setattr("config.SELF_HEALING_AUTO_APPLY", True)
    original = (tmp_repo / "buggy.py").read_text(encoding="utf-8")

    fake_diagnosis = {
        "root_cause": "x", "confidence": "high",
        "file": "buggy.py", "fix_content": "def f(:\n    invalide\n",
    }
    with patch("scripts.self_healing.diagnose_crash", new=AsyncMock(return_value=fake_diagnosis)):
        result = await handle_crash_loop("Traceback: x", root=tmp_repo)

    assert result["action"] == "diagnosed_only"
    assert "ne compile pas" in result["patch_reason"]
    assert _commit_count(tmp_repo) == 1  # rien commité
    assert (tmp_repo / "buggy.py").read_text(encoding="utf-8") == original  # contenu restauré


@pytest.mark.asyncio
async def test_auto_apply_rejects_untracked_file(tmp_db, tmp_repo, monkeypatch):
    from scripts.self_healing import handle_crash_loop

    monkeypatch.setattr("config.SELF_HEALING_ENABLED", True)
    monkeypatch.setattr("config.SELF_HEALING_AUTO_APPLY", True)
    (tmp_repo / "untracked.py").write_text("x = 1\n", encoding="utf-8")

    fake_diagnosis = {
        "root_cause": "x", "confidence": "high",
        "file": "untracked.py", "fix_content": "x = 2\n",
    }
    with patch("scripts.self_healing.diagnose_crash", new=AsyncMock(return_value=fake_diagnosis)):
        result = await handle_crash_loop("Traceback: x", root=tmp_repo)

    assert result["action"] == "diagnosed_only"
    assert "non suivi par git" in result["patch_reason"]


@pytest.mark.asyncio
async def test_auto_apply_rejects_path_traversal(tmp_db, tmp_repo, monkeypatch):
    from scripts.self_healing import handle_crash_loop

    monkeypatch.setattr("config.SELF_HEALING_ENABLED", True)
    monkeypatch.setattr("config.SELF_HEALING_AUTO_APPLY", True)

    fake_diagnosis = {
        "root_cause": "x", "confidence": "high",
        "file": "../../../etc/passwd.py", "fix_content": "x = 1\n",
    }
    with patch("scripts.self_healing.diagnose_crash", new=AsyncMock(return_value=fake_diagnosis)):
        result = await handle_crash_loop("Traceback: x", root=tmp_repo)

    assert result["action"] == "diagnosed_only"
    assert "traversal" in result["patch_reason"]


@pytest.mark.asyncio
async def test_auto_apply_rejects_non_python_file(tmp_db, tmp_repo, monkeypatch):
    from scripts.self_healing import handle_crash_loop

    monkeypatch.setattr("config.SELF_HEALING_ENABLED", True)
    monkeypatch.setattr("config.SELF_HEALING_AUTO_APPLY", True)
    fake_diagnosis = {"root_cause": "x", "confidence": "high", "file": "config.json", "fix_content": "{}"}

    with patch("scripts.self_healing.diagnose_crash", new=AsyncMock(return_value=fake_diagnosis)):
        result = await handle_crash_loop("Traceback: x", root=tmp_repo)

    assert "seuls les fichiers .py" in result["patch_reason"]


@pytest.mark.asyncio
async def test_no_patch_when_diagnosis_incomplete(tmp_db, tmp_repo, monkeypatch):
    from scripts.self_healing import handle_crash_loop

    monkeypatch.setattr("config.SELF_HEALING_ENABLED", True)
    monkeypatch.setattr("config.SELF_HEALING_AUTO_APPLY", True)
    fake_diagnosis = {"root_cause": "cause floue", "confidence": "low", "file": None, "fix_content": None}

    with patch("scripts.self_healing.diagnose_crash", new=AsyncMock(return_value=fake_diagnosis)):
        result = await handle_crash_loop("Traceback: x", root=tmp_repo)

    assert result["action"] == "diagnosed_only"
    assert "patch_reason" not in result


# ── Rollback automatique + cooldown ───────────────────────────

@pytest.mark.asyncio
async def test_rollback_on_recurrence_after_patch(tmp_db, tmp_repo, monkeypatch):
    from scripts.self_healing import _load_state, _now_iso, _save_state, handle_crash_loop

    monkeypatch.setattr("config.SELF_HEALING_ENABLED", True)
    monkeypatch.setattr("config.SELF_HEALING_REGRESSION_WINDOW_MIN", 15)

    # Simule un patch précédent : un vrai commit à annuler.
    (tmp_repo / "buggy.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "self-healing: correctif automatique (buggy.py)"],
                   cwd=tmp_repo, check=True)
    patch_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_repo, capture_output=True, text=True).stdout.strip()
    _save_state({"last_patch_commit": patch_sha, "last_patch_at": _now_iso()})

    diagnose_mock = AsyncMock()
    with patch("scripts.self_healing.diagnose_crash", diagnose_mock):
        result = await handle_crash_loop("Traceback: encore ZeroDivisionError", root=tmp_repo)

    assert result["action"] == "rolled_back"
    assert result["reverted_commit"] == patch_sha
    diagnose_mock.assert_not_called()  # pas de nouveau diagnostic, priorité au rollback

    state = _load_state()
    assert state["last_patch_commit"] is None
    assert state["cooldown_started_at"] is not None
    assert _commit_count(tmp_repo) == 3  # init, patch, revert


@pytest.mark.asyncio
async def test_no_rollback_when_patch_is_old(tmp_db, tmp_repo, monkeypatch):
    """Un patch ancien (hors fenêtre de surveillance) ne déclenche pas de rollback."""
    from datetime import datetime, timedelta

    from scripts.self_healing import _save_state, handle_crash_loop

    monkeypatch.setattr("config.SELF_HEALING_ENABLED", True)
    monkeypatch.setattr("config.SELF_HEALING_AUTO_APPLY", False)
    monkeypatch.setattr("config.SELF_HEALING_REGRESSION_WINDOW_MIN", 15)

    old_ts = (datetime.now() - timedelta(minutes=60)).isoformat(timespec="seconds")
    _save_state({"last_patch_commit": "deadbeef", "last_patch_at": old_ts})

    fake_diagnosis = {"root_cause": "autre chose", "confidence": "medium", "file": None, "fix_content": None}
    with patch("scripts.self_healing.diagnose_crash", new=AsyncMock(return_value=fake_diagnosis)):
        result = await handle_crash_loop("Traceback: différent problème", root=tmp_repo)

    assert result["action"] == "diagnosed_only"  # pas de rollback, diagnostic normal


@pytest.mark.asyncio
async def test_cooldown_blocks_new_diagnosis(tmp_db, tmp_repo, monkeypatch):
    from scripts.self_healing import _now_iso, _save_state, handle_crash_loop

    monkeypatch.setattr("config.SELF_HEALING_ENABLED", True)
    monkeypatch.setattr("config.SELF_HEALING_COOLDOWN_MIN", 60)
    _save_state({"cooldown_started_at": _now_iso()})

    diagnose_mock = AsyncMock()
    with patch("scripts.self_healing.diagnose_crash", diagnose_mock):
        result = await handle_crash_loop("Traceback: x", root=tmp_repo)

    assert result["ok"] is False
    assert "cooldown" in result["reason"]
    diagnose_mock.assert_not_called()


# ── diagnose_crash isolé ───────────────────────────────────────

@pytest.mark.asyncio
async def test_diagnose_crash_parses_llm_response():
    from scripts.self_healing import diagnose_crash

    fake_response = {"content": '{"root_cause": "fuite mémoire", "confidence": "medium", "file": null, "fix_content": null}'}
    with patch("llm.chat", new=AsyncMock(return_value=fake_response)):
        result = await diagnose_crash("Traceback: MemoryError")
    assert result["root_cause"] == "fuite mémoire"


@pytest.mark.asyncio
async def test_diagnose_crash_never_raises_on_llm_failure():
    from scripts.self_healing import diagnose_crash

    with patch("llm.chat", new=AsyncMock(side_effect=RuntimeError("DeepSeek down"))):
        result = await diagnose_crash("Traceback: x")
    assert result["confidence"] == "low"
    assert result["file"] is None

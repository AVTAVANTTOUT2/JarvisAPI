"""Contrats des locks Python reproductibles."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_python_locks_match_sources_and_are_hashed() -> None:
    result = subprocess.run(
        [sys.executable, "tools/update_python_locks.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "6 locks Python vérifiés" in result.stdout


def test_ci_installs_only_hashed_lock_profiles() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    # Backend, Apple, Android et preuve de retrait utilisent tous un lock hashé.
    assert workflow.count("python -m pip install --require-hashes") == 4
    assert "pip install \\\n" not in workflow

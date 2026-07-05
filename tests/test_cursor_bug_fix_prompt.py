"""Tests du gabarit Cursor bug fix dans les system prompts."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_cursor_bug_fix_prompt_file_exists() -> None:
    path = PROJECT_ROOT / "prompts" / "cursor_bug_fix.txt"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "CURSOR PROMPT" in content
    assert "{DESCRIPTION_BUG}" in content
    assert "{LOGS_ICI}" in content
    assert "{LISTE_FICHIERS}" in content


def test_devops_agent_loads_cursor_bug_fix_appendix() -> None:
    from agents.devops import devops_agent

    prompt = devops_agent.load_prompt()
    assert "CURSOR PROMPT" in prompt
    assert "Correction Bug" in prompt
    assert "fix: {résumé court}" in prompt


def test_devops_txt_references_bug_fix_gabarit() -> None:
    content = (PROJECT_ROOT / "prompts" / "devops.txt").read_text(encoding="utf-8")
    assert "cursor_bug_fix.txt" in content


def test_agent_txt_references_bug_fix_gabarit() -> None:
    content = (PROJECT_ROOT / "prompts" / "agent.txt").read_text(encoding="utf-8")
    assert "cursor_bug_fix.txt" in content

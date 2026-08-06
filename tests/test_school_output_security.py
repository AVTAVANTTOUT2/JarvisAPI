"""Régressions sécurité — écriture des devoirs agent École."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _install_school_stubs() -> None:
    agents = types.ModuleType("agents")
    display_text = types.ModuleType("agents.display_text")

    class BaseAgent:  # noqa: D101 — stub minimal pour import agents.school
        name = "school"
        model = "stub"

        async def _route_task(self, *args, **kwargs):
            return {}

    def finalize_assistant_display_text(text: str) -> str:
        return text

    display_text.finalize_assistant_display_text = finalize_assistant_display_text
    agents.BaseAgent = BaseAgent
    sys.modules["agents"] = agents
    sys.modules["agents.display_text"] = display_text


def _load_school_module():
    _install_school_stubs()
    spec = importlib.util.spec_from_file_location(
        "school_agent_module",
        REPO_ROOT / "agents" / "school.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def school_mod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    out = tmp_path / "school"
    out.mkdir()
    monkeypatch.setattr("config.SCHOOL_OUTPUT_DIR", str(out))
    mod = _load_school_module()
    return mod, out


class TestSafeSchoolOutputPath:
    def test_parent_traversal_stays_inside_subject(self, school_mod) -> None:
        mod, out = school_mod
        subject = out / "math"
        subject.mkdir()
        target = mod._safe_school_output_path(subject, "../../../.env")
        assert target is not None
        assert target.name == "env"
        assert target.resolve().is_relative_to(subject.resolve())

    def test_absolute_path_stays_inside_subject(self, school_mod) -> None:
        mod, out = school_mod
        subject = out / "math"
        subject.mkdir()
        target = mod._safe_school_output_path(subject, "/etc/passwd")
        assert target is not None
        assert target.name == "passwd"
        assert target.resolve().is_relative_to(subject.resolve())

    def test_accepts_simple_filename(self, school_mod) -> None:
        mod, out = school_mod
        subject = out / "math"
        subject.mkdir()
        target = mod._safe_school_output_path(subject, "devoir.md")
        assert target is not None
        assert target.name == "devoir.md"
        assert target.parent == subject.resolve()


class TestSchoolAgentSaveBlock:
    def test_save_block_rejects_traversal_filename(self, school_mod) -> None:
        mod, out = school_mod
        agent = mod.SchoolAgent()
        outside = out.parent / "pwned.txt"
        response = (
            "Contenu du devoir.\n"
            "```save\n"
            '{"action":"save","filename":"../../pwned.txt","subject":"math"}\n'
            "```"
        )
        saved = agent._save_school_file(response)
        assert saved is not None
        assert saved.resolve().is_relative_to(out.resolve())
        assert not outside.exists()

    def test_save_block_writes_inside_subject_dir(self, school_mod) -> None:
        mod, out = school_mod
        agent = mod.SchoolAgent()
        response = (
            "# Devoir\n\nTexte.\n"
            "```save\n"
            '{"action":"save","filename":"dissertation.md","subject":"droit"}\n'
            "```"
        )
        saved = agent._save_school_file(response)
        assert saved is not None
        assert saved.is_file()
        assert saved.resolve().is_relative_to(out.resolve())
        assert "droit" in saved.parts
        assert saved.read_text(encoding="utf-8").startswith("# Devoir")

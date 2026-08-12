from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from tools.run_integration_ci import discover_hooks
from integrations.opencode.tools.ci import phase_commands


def test_ci_hook_is_discovered_only_from_manifest() -> None:
    root = Path(__file__).resolve().parents[3]

    assert discover_hooks(root, "offline") == [
        ("opencode", "integrations.opencode.tools.ci")
    ]


def test_removal_ci_hook_always_runs_the_full_proof() -> None:
    assert phase_commands("removal") == (
        ("-m", "integrations.opencode.tools.removal_proof", "--full"),
    )
    assert phase_commands("removal", full_removal=False) == (
        ("-m", "integrations.opencode.tools.removal_proof"),
    )


def test_workflow_has_a_dedicated_full_removal_gate() -> None:
    root = Path(__file__).resolve().parents[3]
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "removal_proof:" in workflow
    assert "name: Preuve de retrait complète (sans provider)" in workflow
    assert "run: python tools/run_integration_ci.py --phase removal" in workflow
    assert workflow.count("tools/run_integration_ci.py --phase removal") == 1
    assert workflow.count("--quick-removal") == 1
    assert len(config._JARVIS_REQUIRED_CHECKS_DEFAULT) == 7
    for check_name in config._JARVIS_REQUIRED_CHECKS_DEFAULT:
        assert f"name: {check_name}" in workflow


def test_ci_runner_survives_plugin_removal(tmp_path: Path) -> None:
    (tmp_path / "integrations").mkdir()

    assert discover_hooks(tmp_path, "offline") == []


def test_ci_runner_rejects_cross_plugin_entrypoint(tmp_path: Path) -> None:
    plugin = tmp_path / "integrations" / "provider"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps(
            {
                "ci": {
                    "module": "integrations.other.tools.ci",
                    "phases": ["offline"],
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="hook CI"):
        discover_hooks(tmp_path, "offline")

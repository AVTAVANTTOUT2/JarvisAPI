from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "apply_cursor_engineering_contract.py"
)
SPEC = importlib.util.spec_from_file_location("cursor_contract", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_inject_contract_is_idempotent() -> None:
    first = MODULE.inject_contract("Original prompt", "Find critical bugs")
    second = MODULE.inject_contract(first, "Find critical bugs")
    assert first == second
    assert first.count(MODULE.START_MARKER) == 1
    assert "cursor-finding" in first
    assert "HANDOFF=codex" in first


def test_build_updates_only_enabled_technical_automations(tmp_path: Path) -> None:
    source = tmp_path / "payloads.json"
    source.write_text(
        json.dumps(
            {
                "Find critical bugs": {
                    "enabled": True,
                    "workflow": {"prompts": [{"prompt": "bugs"}]},
                },
                "Product FAQ Agent": {
                    "enabled": True,
                    "workflow": {"prompts": [{"prompt": "faq"}]},
                },
                "Generate docs": {
                    "enabled": False,
                    "workflow": {"prompts": [{"prompt": "docs"}]},
                },
            }
        ),
        encoding="utf-8",
    )
    updates = MODULE.build_updates(source)
    assert list(updates) == ["Find critical bugs"]
    assert (
        "JARVIS_ENGINEERING_HANDOFF_V1"
        in updates["Find critical bugs"]["workflow"]["prompts"][0]["prompt"]
    )

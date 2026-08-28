from __future__ import annotations

import json

import pytest

from integrations.opencode.scripts.task_control_delivery_live import main


def test_live_runner_reports_not_executed_without_deepseek_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    result = main(
        [
            "plan",
            "--repo",
            "/path/that/must/not/be-touched",
            "--title",
            "Fixture",
            "--request",
            "Corriger la fixture",
            "--test",
            "python3 -m pytest -q",
            "--idempotency-key",
            "missing-key-proof",
        ]
    )

    assert result == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": False,
        "production": "NOT_EXECUTED",
        "reason": "DEEPSEEK_API_KEY absente de l'environnement du processus",
        "runtime": "opencode@1.18.16",
    }

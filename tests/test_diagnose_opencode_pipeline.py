"""Diagnostic du pipeline OpenCode : flags, binaire, timeout, logs, sans secrets."""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.opencode.config import RuntimeLayout
from scripts.diagnose_opencode_pipeline import diagnose, probe_binary


def _layout(tmp_path: Path) -> RuntimeLayout:
    root = tmp_path / "plugin"
    root.mkdir()
    layout = RuntimeLayout.from_integration_root(root)
    layout.ensure()
    return layout


def _write_binary(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)


def test_probe_reports_missing_binary(tmp_path: Path) -> None:
    result = probe_binary(tmp_path / "missing-opencode", timeout_seconds=0.5)
    assert result["status"] == "FAIL"
    assert result["code"] == "binary_absent"


def test_probe_reports_timeout(tmp_path: Path) -> None:
    binary = tmp_path / "opencode"
    _write_binary(binary, "#!/bin/sh\nexec sleep 30\n")
    result = probe_binary(binary, timeout_seconds=0.05)
    assert result["status"] == "FAIL"
    assert result["code"] == "timeout"


def test_probe_reports_nonzero_exit(tmp_path: Path) -> None:
    binary = tmp_path / "opencode"
    _write_binary(binary, "#!/bin/sh\nexit 3\n")
    result = probe_binary(binary, timeout_seconds=2.0)
    assert result["status"] == "FAIL"
    assert result["code"] == "binary_exec_failed"


def test_probe_reports_success(tmp_path: Path) -> None:
    binary = tmp_path / "opencode"
    _write_binary(binary, "#!/bin/sh\necho 1.18.16\n")
    result = probe_binary(binary, timeout_seconds=2.0)
    assert result["status"] == "PASS"
    assert result["version"] == "1.18.16"


def test_diagnose_disabled_runtime_is_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config

    monkeypatch.setattr(config, "AGENTIC_RUNTIME", "disabled")
    layout = _layout(tmp_path)
    report = diagnose(layout=layout, timeout_seconds=1.0, model_key_present=True)
    assert report["status"] == "FAIL"
    codes = {item["code"] for item in report["checks"]}
    assert "runtime_disabled" in codes
    dumped = str(report)
    assert "sk-" not in dumped


def test_diagnose_missing_binary_is_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config

    monkeypatch.setattr(config, "AGENTIC_RUNTIME", "auto")
    layout = _layout(tmp_path)
    report = diagnose(layout=layout, timeout_seconds=1.0, model_key_present=True)
    assert report["status"] == "FAIL"
    codes = {item["code"] for item in report["checks"]}
    assert "binary_absent" in codes


def test_diagnose_success_and_idle_serve_are_not_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config

    monkeypatch.setattr(config, "AGENTIC_RUNTIME", "auto")
    monkeypatch.setattr(config, "AGENTIC_REQUIRE_PLAN_APPROVAL", False)
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/opencode")

    layout = _layout(tmp_path)
    _write_binary(layout.binary_path, "#!/bin/sh\necho 1.18.16\n")
    (layout.logs_dir / "server.stdout.log").write_text("started\n", encoding="utf-8")

    report = diagnose(layout=layout, timeout_seconds=2.0, model_key_present=True)
    by_name = {item["name"]: item for item in report["checks"]}
    assert by_name["binary_probe"]["status"] == "PASS"
    assert by_name["install"]["status"] == "PASS"
    assert by_name["serve"]["status"] == "WARN"
    assert by_name["serve"]["code"] == "serve_idle"
    assert by_name["logs"]["status"] == "PASS"
    assert by_name["model_key"]["status"] == "PASS"
    assert report["status"] in {"PASS", "WARN"}
    assert "sk-" not in str(report)

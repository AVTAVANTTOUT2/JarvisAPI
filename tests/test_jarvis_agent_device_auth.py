"""Contrat d'authentification et persistance du jeton de jarvis_agent."""

from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from scripts.jarvis_agent import JarvisAgent


def _agent(tmp_path: Path, **kwargs) -> JarvisAgent:
    with patch("scripts.jarvis_agent.subprocess.check_output") as check_output:
        check_output.side_effect = [b"macbook-test\n", b"MacBook Test\n"]
        return JarvisAgent(
            "https://jarvis.test",
            device_id=None,
            token_file=tmp_path / "device.token",
            **kwargs,
        )


def test_pairing_persists_token_without_printing_it(tmp_path, capsys):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"ok": True, "token": "raw-device-token"}

    agent = _agent(tmp_path, pairing_code="123456")
    with patch("scripts.jarvis_agent.requests.post", return_value=response) as post:
        agent._register("123456")

    assert agent.token == "raw-device-token"
    assert agent.headers == {"X-Device-Token": "raw-device-token"}
    assert agent.token_file.read_text(encoding="utf-8").strip() == "raw-device-token"
    assert stat.S_IMODE(agent.token_file.stat().st_mode) == 0o600
    assert post.call_args.kwargs["json"]["pairing_code"] == "123456"
    assert "raw-device-token" not in capsys.readouterr().out


def test_saved_token_is_loaded_and_sent_with_device_header(tmp_path):
    token_file = tmp_path / "device.token"
    token_file.write_text("saved-token\n", encoding="utf-8")
    token_file.chmod(0o644)

    agent = _agent(tmp_path)

    assert agent.token == "saved-token"
    assert agent.headers == {"X-Device-Token": "saved-token"}
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


def test_credential_probe_uses_same_header_contract(tmp_path):
    response = Mock()
    response.raise_for_status.return_value = None
    agent = _agent(tmp_path, auth_token="rotated-token")

    with patch("scripts.jarvis_agent.requests.post", return_value=response) as post:
        agent._verify_credentials()

    assert post.call_args.kwargs["headers"] == {"X-Device-Token": "rotated-token"}
    assert post.call_args.args[0].endswith("/api/devices/macbook-test/heartbeat")


def test_invalid_cli_token_does_not_overwrite_saved_credentials(tmp_path):
    token_file = tmp_path / "device.token"
    token_file.write_text("known-good-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    agent = _agent(tmp_path, auth_token="invalid-new-token")

    with patch.object(
        agent,
        "_verify_credentials",
        side_effect=RuntimeError("invalid"),
    ), pytest.raises(RuntimeError):
        agent.start()

    assert token_file.read_text(encoding="utf-8").strip() == "known-good-token"

"""Contrat d'authentification et persistance du jeton de jarvis_agent."""

from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from scripts.jarvis_agent import JarvisAgent, detect_tailscale_ip, validate_server_url


def _agent(tmp_path: Path, **kwargs) -> JarvisAgent:
    with patch("scripts.jarvis_agent.subprocess.check_output") as check_output, patch(
        "scripts.jarvis_agent.detect_tailscale_ip", return_value=None
    ):
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
    assert post.call_args.kwargs["json"]["ip_tailscale"] is None
    assert post.call_args.kwargs["allow_redirects"] is False
    assert post.call_args.kwargs["verify"] is True
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
    assert post.call_args.kwargs["allow_redirects"] is False
    assert post.call_args.kwargs["verify"] is True


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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://127.0.0.1:8081/", "http://127.0.0.1:8081"),
        ("http://[::1]:8081", "http://[::1]:8081"),
        ("http://localhost:8081", "http://localhost:8081"),
        ("https://jarvis.example.test/", "https://jarvis.example.test"),
        ("https://100.100.20.30:8081", "https://100.100.20.30:8081"),
    ],
)
def test_server_url_accepts_only_tls_or_loopback(raw, expected):
    assert validate_server_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "http://100.100.20.30:8081",
        "http://192.168.1.10:8081",
        "http://jarvis.example.test",
        "ftp://jarvis.example.test",
        "jarvis.example.test",
        "https://user:secret@jarvis.example.test",
        "https://jarvis.example.test/api",
        "https://jarvis.example.test?token=secret",
    ],
)
def test_server_url_rejects_cleartext_remote_and_ambiguous_urls(raw):
    with pytest.raises(ValueError):
        validate_server_url(raw)


def test_redirect_is_rejected_before_credentials_can_follow(tmp_path):
    response = Mock(status_code=307)
    agent = _agent(tmp_path, auth_token="rotated-token")

    with patch("scripts.jarvis_agent.requests.post", return_value=response), pytest.raises(
        RuntimeError, match="Redirection serveur refusée"
    ):
        agent._verify_credentials()


def test_explicit_ca_bundle_is_used_for_hostname_verification(tmp_path):
    ca_bundle = tmp_path / "private-ca.pem"
    ca_bundle.write_text("TEST CA", encoding="utf-8")
    response = Mock(status_code=200)
    response.raise_for_status.return_value = None

    with patch("scripts.jarvis_agent.subprocess.check_output") as check_output, patch(
        "scripts.jarvis_agent.detect_tailscale_ip", return_value=None
    ):
        check_output.side_effect = [b"macbook-test\n", b"MacBook Test\n"]
        agent = JarvisAgent(
            "https://jarvis.example.test",
            auth_token="token",
            token_file=tmp_path / "device.token",
            ca_bundle=ca_bundle,
        )

    with patch("scripts.jarvis_agent.requests.post", return_value=response) as post:
        agent._verify_credentials()

    assert post.call_args.kwargs["verify"] == str(ca_bundle)


def test_missing_ca_bundle_is_refused(tmp_path):
    with pytest.raises(ValueError, match="Bundle CA introuvable"):
        JarvisAgent(
            "https://jarvis.example.test",
            token_file=tmp_path / "device.token",
            ca_bundle=tmp_path / "missing.pem",
        )


def test_tailscale_ip_is_detected_and_registered(tmp_path):
    response = Mock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = {"token": "issued-token"}

    with patch("scripts.jarvis_agent.subprocess.check_output") as check_output:
        check_output.side_effect = [
            b"macbook-test\n",
            b"MacBook Test\n",
            b"100.100.20.30\n",
        ]
        agent = JarvisAgent(
            "https://jarvis.example.test",
            pairing_code="123456",
            token_file=tmp_path / "device.token",
        )

    with patch("scripts.jarvis_agent.requests.post", return_value=response) as post:
        agent._register("123456")

    assert agent.ip_tailscale == "100.100.20.30"
    assert post.call_args.kwargs["json"]["ip_tailscale"] == "100.100.20.30"


def test_non_tailscale_cgnat_address_is_not_advertised():
    with patch(
        "scripts.jarvis_agent.subprocess.check_output",
        return_value=b"192.168.1.10\n",
    ):
        assert detect_tailscale_ip() is None

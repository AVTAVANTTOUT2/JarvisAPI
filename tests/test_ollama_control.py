"""Tests health-check et sélection de modèles Ollama (sans serveur réel)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from integrations.ollama_control import (
    check_ollama_health,
    pick_vision_model,
    start_ollama,
    stop_ollama,
)


def test_pick_vision_model_prefers_configured() -> None:
    installed = ["llama3:8b", "qwen2.5-vl:7b", "llava:7b"]
    assert pick_vision_model(installed, "qwen2.5-vl:7b") == "qwen2.5-vl:7b"


def test_pick_vision_model_normalizes_qwen25vl_alias() -> None:
    installed = ["qwen2.5vl:7b"]
    assert pick_vision_model(installed, "qwen2.5-vl:7b") == "qwen2.5vl:7b"


def test_pick_vision_model_fallback_when_preferred_missing() -> None:
    installed = ["llava:7b", "llama3:8b"]
    assert pick_vision_model(installed, "qwen2.5-vl:7b") == "llava:7b"


def test_pick_vision_model_none_without_vision() -> None:
    assert pick_vision_model(["llama3:8b", "mistral:7b"], "qwen2.5-vl:7b") is None


def test_pick_vision_model_does_not_confuse_qwen3_with_qwen3_vl() -> None:
    assert pick_vision_model(["qwen3:8b", "qwen2.5:7b"], "qwen3-vl:4b") is None


def test_check_ollama_health_healthy_with_vision_model() -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "models": [{"name": "qwen2.5-vl:7b", "size": 1, "details": {"family": "qwen"}}]
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get.return_value = mock_resp

    with patch("integrations.ollama_control.httpx.Client", return_value=mock_client):
        with patch("integrations.ollama_control.configured_vision_model", return_value="qwen2.5-vl:7b"):
            health = check_ollama_health()

    assert health["healthy"] is True
    assert health["status"] == "running"
    assert health["vision_model_available"] is True
    assert health["latency_ms"] is not None


def test_check_ollama_health_process_alive_but_api_dead() -> None:
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get.side_effect = httpx.ConnectError("connection refused")

    with patch("integrations.ollama_control.httpx.Client", return_value=mock_client):
        health = check_ollama_health()

    assert health["healthy"] is False
    assert health["status"] == "stopped"
    assert health["error"]


def test_check_ollama_health_api_ok_but_no_vision_model() -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"models": [{"name": "llama3:8b", "size": 1, "details": {}}]}
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get.return_value = mock_resp

    with patch("integrations.ollama_control.httpx.Client", return_value=mock_client):
        with patch("integrations.ollama_control.configured_vision_model", return_value="qwen2.5-vl:7b"):
            health = check_ollama_health()

    assert health["healthy"] is True
    assert health["vision_model_available"] is False
    assert "aucun modèle vision" in (health["error"] or "").lower()


def test_start_ollama_noop_when_already_healthy() -> None:
    healthy = {
        "service": "ollama",
        "status": "running",
        "healthy": True,
        "latency_ms": 12,
        "vision_model_available": True,
    }
    with patch("integrations.ollama_control.check_ollama_health", return_value=healthy):
        result = start_ollama()
    assert result["ok"] is True
    assert "déjà" in result["message"].lower()


def test_start_ollama_timeout() -> None:
    unhealthy = {
        "service": "ollama",
        "status": "stopped",
        "healthy": False,
        "error": "connection refused",
    }
    fake_proc = MagicMock()
    fake_proc.pid = 4242
    fake_proc.poll.return_value = None

    times = iter([0.0, 0.1, 0.2, 100.0, 100.1, 100.2])

    with (
        patch("integrations.ollama_control.check_ollama_health", return_value=unhealthy),
        patch("integrations.ollama_control.subprocess.Popen", return_value=fake_proc),
        patch("integrations.ollama_control._write_pidfile"),
        patch("integrations.ollama_control.time.sleep"),
        patch("integrations.ollama_control.time.time", side_effect=lambda: next(times, 200.0)),
    ):
        result = start_ollama(wait_s=1)

    assert result["ok"] is False
    assert "timeout" in result["message"].lower()


def test_stop_ollama_already_stopped() -> None:
    with (
        patch("integrations.ollama_control._find_ollama_serve_pids", return_value=[]),
        patch("integrations.ollama_control._read_pidfile", return_value=None),
        patch(
            "integrations.ollama_control.check_ollama_health",
            return_value={"healthy": False, "status": "stopped"},
        ),
        patch("integrations.ollama_control._clear_pidfile"),
    ):
        result = stop_ollama()
    assert result["ok"] is True
    assert result["status"] == "stopped"

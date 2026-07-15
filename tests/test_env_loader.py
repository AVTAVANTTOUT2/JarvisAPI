"""Tests du chargeur d'environnement (.env.config + .env)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_load_jarvis_env_reads_config_then_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path / ".env.config"
    secrets_file = tmp_path / ".env"
    config_file.write_text("WEB_PORT=9001\nUSER_NAME=ConfigUser\n", encoding="utf-8")
    secrets_file.write_text(
        "DEEPSEEK_API_KEY=sk-test-secret\nUSER_NAME=SecretUser\n",
        encoding="utf-8",
    )

    import env_loader

    importlib.reload(env_loader)
    monkeypatch.setattr(env_loader, "BASE_DIR", tmp_path)
    monkeypatch.setattr(env_loader, "CONFIG_ENV_FILE", config_file)
    monkeypatch.setattr(env_loader, "SECRETS_ENV_FILE", secrets_file)
    env_loader._ENV_LOADED = False
    monkeypatch.delenv("WEB_PORT", raising=False)
    monkeypatch.delenv("USER_NAME", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    env_loader.load_jarvis_env(force=True)

    import os

    assert os.environ["WEB_PORT"] == "9001"
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-test-secret"
    assert os.environ["USER_NAME"] == "SecretUser"


def test_legacy_single_env_still_works(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secrets_file = tmp_path / ".env"
    secrets_file.write_text("DEEPSEEK_API_KEY=sk-legacy\nWEB_PORT=8082\n", encoding="utf-8")

    import env_loader

    importlib.reload(env_loader)
    monkeypatch.setattr(env_loader, "BASE_DIR", tmp_path)
    monkeypatch.setattr(env_loader, "CONFIG_ENV_FILE", tmp_path / ".env.config")
    monkeypatch.setattr(env_loader, "SECRETS_ENV_FILE", secrets_file)
    env_loader._ENV_LOADED = False
    monkeypatch.delenv("WEB_PORT", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    env_loader.load_jarvis_env(force=True)

    import os

    assert os.environ["DEEPSEEK_API_KEY"] == "sk-legacy"
    assert os.environ["WEB_PORT"] == "8082"

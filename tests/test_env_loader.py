"""Tests du chargeur d'environnement (.env.config + .env)."""

from __future__ import annotations

import importlib
import os
import stat
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

    assert os.environ["WEB_PORT"] == "9001"
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-test-secret"
    assert os.environ["USER_NAME"] == "SecretUser"


def test_legacy_single_env_still_works(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secrets_file = tmp_path / ".env"
    secrets_file.write_text(
        "DEEPSEEK_API_KEY=sk-legacy\nWEB_PORT=8082\n",
        encoding="utf-8",
    )

    import env_loader

    importlib.reload(env_loader)
    monkeypatch.setattr(env_loader, "BASE_DIR", tmp_path)
    monkeypatch.setattr(env_loader, "CONFIG_ENV_FILE", tmp_path / ".env.config")
    monkeypatch.setattr(env_loader, "SECRETS_ENV_FILE", secrets_file)
    env_loader._ENV_LOADED = False
    monkeypatch.delenv("WEB_PORT", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    env_loader.load_jarvis_env(force=True)

    assert os.environ["DEEPSEEK_API_KEY"] == "sk-legacy"
    assert os.environ["WEB_PORT"] == "8082"


@pytest.mark.parametrize(
    "secret_key",
    sorted(
        {
            "DEEPSEEK_API_KEY",
            "WEATHER_API_KEY",
            "TAVILY_API_KEY",
            "PORCUPINE_ACCESS_KEY",
            "LOCATION_API_TOKEN",
            "BACKUP_ENCRYPTION_PASSPHRASE",
        }
    ),
)
def test_config_file_refuses_secret_keys_even_when_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    secret_key: str,
) -> None:
    config_file = tmp_path / ".env.config"
    secrets_file = tmp_path / ".env"
    config_file.write_text(f"WEB_PORT=9001\n{secret_key}=\n", encoding="utf-8")
    secrets_file.write_text("DEEPSEEK_API_KEY=sk-safe\n", encoding="utf-8")

    import env_loader

    importlib.reload(env_loader)
    monkeypatch.setattr(env_loader, "CONFIG_ENV_FILE", config_file)
    monkeypatch.setattr(env_loader, "SECRETS_ENV_FILE", secrets_file)
    monkeypatch.delenv(secret_key, raising=False)
    monkeypatch.delenv("WEB_PORT", raising=False)

    with pytest.raises(env_loader.EnvironmentPolicyError, match=secret_key):
        env_loader.load_jarvis_env(force=True)

    assert secret_key not in os.environ
    assert "WEB_PORT" not in os.environ


def test_config_example_contains_no_secret_keys() -> None:
    import env_loader

    example_values = env_loader.dotenv_values(PROJECT_ROOT / ".env.config.example")

    assert env_loader.SECRET_ENV_KEYS.isdisjoint(example_values)


def test_env_files_are_hardened_before_loading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / ".env.config"
    secrets_file = tmp_path / ".env"
    config_file.write_text("WEB_PORT=9003\n", encoding="utf-8")
    secrets_file.write_text("DEEPSEEK_API_KEY=sk-private\n", encoding="utf-8")
    config_file.chmod(0o644)
    secrets_file.chmod(0o644)

    import env_loader

    importlib.reload(env_loader)
    monkeypatch.setattr(env_loader, "CONFIG_ENV_FILE", config_file)
    monkeypatch.setattr(env_loader, "SECRETS_ENV_FILE", secrets_file)
    monkeypatch.delenv("WEB_PORT", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    env_loader.load_jarvis_env(force=True)

    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(secrets_file.stat().st_mode) == 0o600


def test_symlinked_env_file_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "outside.env"
    target.write_text("WEB_PORT=9004\n", encoding="utf-8")
    config_file = tmp_path / ".env.config"
    config_file.symlink_to(target)

    import env_loader

    importlib.reload(env_loader)
    monkeypatch.setattr(env_loader, "CONFIG_ENV_FILE", config_file)
    monkeypatch.setattr(env_loader, "SECRETS_ENV_FILE", tmp_path / ".env")

    with pytest.raises(RuntimeError, match="lien symbolique"):
        env_loader.load_jarvis_env(force=True)

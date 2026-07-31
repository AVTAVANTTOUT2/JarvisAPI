"""Le bootstrap refuse un service faussement sain sans clé LLM."""

from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI

import config


@pytest.mark.parametrize("value", ["", "   ", "sk-..."])
def test_required_runtime_config_rejects_missing_or_placeholder_key(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", value)

    with pytest.raises(config.ConfigurationError, match="DEEPSEEK_API_KEY est obligatoire"):
        config.validate_required_runtime_config()


def test_required_runtime_config_accepts_configured_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test-key-not-a-secret")

    config.validate_required_runtime_config()


@pytest.mark.asyncio
async def test_lifespan_fails_before_database_initialization_without_llm_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifespan_module = importlib.import_module("api.lifespan")
    database_initialized = False

    def _unexpected_init_db() -> None:
        nonlocal database_initialized
        database_initialized = True

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(lifespan_module, "init_db", _unexpected_init_db)

    with pytest.raises(config.ConfigurationError):
        async with lifespan_module.lifespan(FastAPI()):
            pass

    assert database_initialized is False
